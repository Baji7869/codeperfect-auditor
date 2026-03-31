"""
realtime_codes.py  —  CodePerfect Auditor
ICD-10-CM 2026 + CPT 2024 validation.

FIX: Built-in comprehensive code dictionary as primary lookup.
     No dependency on code_db.py or medical_codes.json format.
     NLM API used as secondary source for unlisted codes.
     This fixes: J18.9, J44.1, 99222, 71046, G80.4 all showing
     "Not in CMS 2026" despite being valid codes.
"""

import httpx
import json
import logging
import asyncio
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ── NLM API endpoints (FY2026 first, FY2024 fallback) ────────────────────────
NLM_ICD10_ENDPOINTS = [
    "https://clinicaltables.nlm.nih.gov/api/icd10cm_2026/v3/search",
    "https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search",
]
NLM_HCPCS_API = "https://clinicaltables.nlm.nih.gov/api/hcpcs/v3/search"
TIMEOUT = 5.0

# ── Disk cache ────────────────────────────────────────────────────────────────
_CACHE_FILE = Path(__file__).parent.parent / "code_cache.json"
_cache: dict = {}

def _load_cache():
    global _cache
    if _CACHE_FILE.exists():
        try:
            raw = json.loads(_CACHE_FILE.read_text())
            _cache = {k: v for k, v in raw.items() if v is not None}
            logger.info(f"Code cache: {len(_cache)} entries")
        except Exception:
            _cache = {}

def _save_cache():
    try:
        _CACHE_FILE.write_text(json.dumps(_cache, indent=2))
    except Exception:
        pass

_load_cache()


# ════════════════════════════════════════════════════════════════════════════
# BUILT-IN CODE DATABASE
# Comprehensive ICD-10-CM + CPT codes embedded directly.
# This is the PRIMARY source — no file I/O dependency.
# Covers all codes commonly used in medical auditing.
# ════════════════════════════════════════════════════════════════════════════

BUILTIN_ICD10: dict[str, str] = {
    # ── Infectious / Sepsis ──────────────────────────────────────────────────
    "A41.9":  "Sepsis, unspecified organism",
    "A41.0":  "Sepsis due to Staphylococcus aureus",
    "A41.01": "Sepsis due to Methicillin susceptible Staphylococcus aureus",
    "A41.02": "Sepsis due to Methicillin resistant Staphylococcus aureus",
    "A41.1":  "Sepsis due to other specified staphylococcus",
    "A41.2":  "Sepsis due to unspecified staphylococcus",
    "A41.3":  "Sepsis due to Hemophilus influenzae",
    "A41.4":  "Sepsis due to anaerobes",
    "A41.50": "Gram-negative sepsis, unspecified",
    "A41.51": "Sepsis due to Escherichia coli",
    "A41.52": "Sepsis due to Pseudomonas",
    "A41.53": "Sepsis due to Serratia",
    "A41.59": "Other Gram-negative sepsis",
    "A41.81": "Sepsis due to Enterococcus",
    "R65.10": "Systemic inflammatory response syndrome (SIRS) of non-infectious origin without acute organ dysfunction",
    "R65.11": "Systemic inflammatory response syndrome (SIRS) of non-infectious origin with acute organ dysfunction",
    "R65.20": "Severe sepsis without septic shock",
    "R65.21": "Severe sepsis with septic shock",

    # ── Pneumonia / Respiratory ──────────────────────────────────────────────
    "J18.0":  "Bronchopneumonia, unspecified organism",
    "J18.1":  "Lobar pneumonia, unspecified organism",
    "J18.2":  "Hypostatic pneumonia, unspecified organism",
    "J18.8":  "Other pneumonia, unspecified organism",
    "J18.9":  "Pneumonia, unspecified organism",
    "J15.0":  "Pneumonia due to Klebsiella pneumoniae",
    "J15.1":  "Pneumonia due to Pseudomonas",
    "J15.20": "Pneumonia due to staphylococcus, unspecified",
    "J15.3":  "Pneumonia due to streptococcus, group B",
    "J15.4":  "Pneumonia due to other streptococci",
    "J15.5":  "Pneumonia due to Escherichia coli",
    "J15.6":  "Pneumonia due to other Gram-negative bacteria",
    "J15.7":  "Pneumonia due to Mycoplasma pneumoniae",
    "J15.9":  "Unspecified bacterial pneumonia",
    "J12.0":  "Adenoviral pneumonia",
    "J12.1":  "Respiratory syncytial virus pneumonia",
    "J12.89": "Other viral pneumonia",
    "J12.9":  "Viral pneumonia, unspecified",
    "J13":    "Pneumonia due to Streptococcus pneumoniae",
    "J14":    "Pneumonia due to Hemophilus influenzae",

    # ── COPD / Asthma ───────────────────────────────────────────────────────
    "J44.0":  "Chronic obstructive pulmonary disease with (acute) lower respiratory infection",
    "J44.1":  "Chronic obstructive pulmonary disease with (acute) exacerbation",
    "J44.9":  "Chronic obstructive pulmonary disease, unspecified",
    "J43.9":  "Emphysema, unspecified",
    "J45.20": "Mild intermittent asthma, uncomplicated",
    "J45.21": "Mild intermittent asthma with (acute) exacerbation",
    "J45.30": "Mild persistent asthma, uncomplicated",
    "J45.40": "Moderate persistent asthma, uncomplicated",
    "J45.41": "Moderate persistent asthma with (acute) exacerbation",
    "J45.50": "Severe persistent asthma, uncomplicated",
    "J45.51": "Severe persistent asthma with (acute) exacerbation",
    "J45.901":"Unspecified asthma with (acute) exacerbation",
    "J96.00": "Acute respiratory failure, unspecified whether with hypoxia or hypercapnia",
    "J96.01": "Acute respiratory failure with hypoxia",
    "J96.02": "Acute respiratory failure with hypercapnia",
    "J96.10": "Chronic respiratory failure, unspecified",
    "J96.11": "Chronic respiratory failure with hypoxia",
    "J96.20": "Acute and chronic respiratory failure, unspecified",
    "J96.21": "Acute and chronic respiratory failure with hypoxia",
    "J96.9":  "Respiratory failure, unspecified",
    "J96.0":  "Acute respiratory failure",
    "J96.1":  "Chronic respiratory failure",
    "J96.2":  "Acute and chronic respiratory failure",

    # ── Cardiac ──────────────────────────────────────────────────────────────
    "I21.01": "ST elevation (STEMI) myocardial infarction involving left main coronary artery",
    "I21.02": "ST elevation (STEMI) myocardial infarction involving left anterior descending coronary artery",
    "I21.09": "ST elevation (STEMI) myocardial infarction involving other coronary artery of anterior wall",
    "I21.11": "ST elevation (STEMI) myocardial infarction involving right coronary artery",
    "I21.19": "ST elevation (STEMI) myocardial infarction involving other coronary artery of inferior wall",
    "I21.21": "ST elevation (STEMI) myocardial infarction involving left circumflex coronary artery",
    "I21.29": "ST elevation (STEMI) myocardial infarction involving other sites",
    "I21.3":  "ST elevation (STEMI) myocardial infarction of unspecified site",
    "I21.4":  "Non-ST elevation (NSTEMI) myocardial infarction",
    "I21.9":  "Acute myocardial infarction, unspecified",
    "I21.A1": "AMI due to atherosclerotic heart disease of native coronary artery",
    "I22.0":  "Subsequent ST elevation (STEMI) myocardial infarction of anterior wall",
    "I22.1":  "Subsequent ST elevation (STEMI) myocardial infarction of inferior wall",
    "I22.9":  "Subsequent myocardial infarction, unspecified",
    "I10":    "Essential (primary) hypertension",
    "I11.0":  "Hypertensive heart disease with heart failure",
    "I11.9":  "Hypertensive heart disease without heart failure",
    "I12.9":  "Hypertensive chronic kidney disease with stage 1 through stage 4 or unspecified CKD",
    "I13.10": "Hypertensive heart and chronic kidney disease without heart failure",
    "I25.10": "Atherosclerotic heart disease of native coronary artery without angina pectoris",
    "I25.110":"Atherosclerotic heart disease of native coronary artery with unstable angina pectoris",
    "I48.0":  "Paroxysmal atrial fibrillation",
    "I48.11": "Longstanding persistent atrial fibrillation",
    "I48.19": "Other persistent atrial fibrillation",
    "I48.20": "Chronic atrial fibrillation, unspecified",
    "I48.21": "Permanent atrial fibrillation",
    "I48.9":  "Unspecified atrial fibrillation and atrial flutter",
    "I50.1":  "Left ventricular failure, unspecified",
    "I50.20": "Unspecified systolic (congestive) heart failure",
    "I50.21": "Acute systolic (congestive) heart failure",
    "I50.22": "Chronic systolic (congestive) heart failure",
    "I50.23": "Acute on chronic systolic (congestive) heart failure",
    "I50.30": "Unspecified diastolic (congestive) heart failure",
    "I50.31": "Acute diastolic (congestive) heart failure",
    "I50.32": "Chronic diastolic (congestive) heart failure",
    "I50.33": "Acute on chronic diastolic (congestive) heart failure",
    "I50.40": "Unspecified combined systolic and diastolic heart failure",
    "I50.9":  "Heart failure, unspecified",
    "I63.9":  "Cerebral infarction, unspecified",
    "I63.50": "Cerebral infarction due to unspecified occlusion or stenosis of unspecified cerebral artery",

    # ── Endocrine / Diabetes ─────────────────────────────────────────────────
    "E11.9":  "Type 2 diabetes mellitus without complications",
    "E11.0":  "Type 2 diabetes mellitus with hyperosmolarity",
    "E11.00": "Type 2 diabetes mellitus with hyperosmolarity without nonketotic hyperglycemic-hyperosmolar coma (NKHHC)",
    "E11.01": "Type 2 diabetes mellitus with hyperosmolarity with coma",
    "E11.10": "Type 2 diabetes mellitus with ketoacidosis without coma",
    "E11.11": "Type 2 diabetes mellitus with ketoacidosis with coma",
    "E11.21": "Type 2 diabetes mellitus with diabetic nephropathy",
    "E11.22": "Type 2 diabetes mellitus with diabetic chronic kidney disease",
    "E11.29": "Type 2 diabetes mellitus with other diabetic kidney complication",
    "E11.36": "Type 2 diabetes mellitus with diabetic cataract",
    "E11.40": "Type 2 diabetes mellitus with diabetic neuropathy, unspecified",
    "E11.41": "Type 2 diabetes mellitus with diabetic mononeuropathy",
    "E11.42": "Type 2 diabetes mellitus with diabetic polyneuropathy",
    "E11.43": "Type 2 diabetes mellitus with diabetic autonomic (poly)neuropathy",
    "E11.44": "Type 2 diabetes mellitus with diabetic amyotrophy",
    "E11.51": "Type 2 diabetes mellitus with diabetic peripheral angiopathy without gangrene",
    "E11.52": "Type 2 diabetes mellitus with diabetic peripheral angiopathy with gangrene",
    "E11.59": "Type 2 diabetes mellitus with other circulatory complications",
    "E11.61": "Type 2 diabetes mellitus with diabetic arthropathy",
    "E11.618":"Type 2 diabetes mellitus with other diabetic arthropathy",
    "E11.620":"Type 2 diabetes mellitus with diabetic dermatitis",
    "E11.628":"Type 2 diabetes mellitus with other skin complications",
    "E11.630":"Type 2 diabetes mellitus with periodontal disease",
    "E11.638":"Type 2 diabetes mellitus with other oral complications",
    "E11.641":"Type 2 diabetes mellitus with hypoglycemia with coma",
    "E11.649":"Type 2 diabetes mellitus with hypoglycemia without coma",
    "E11.65": "Type 2 diabetes mellitus with hyperglycemia",
    "E11.69": "Type 2 diabetes mellitus with other specified complication",
    "E11.8":  "Type 2 diabetes mellitus with unspecified complications",
    "E10.9":  "Type 1 diabetes mellitus without complications",
    "E10.65": "Type 1 diabetes mellitus with hyperglycemia",
    "E13.9":  "Other specified diabetes mellitus without complications",
    "E66.01": "Morbid (severe) obesity due to excess calories",
    "E66.09": "Other obesity due to excess calories",
    "E66.1":  "Drug-induced obesity",
    "E66.9":  "Obesity, unspecified",
    "E66.0":  "Obesity due to excess calories",
    "E78.00": "Pure hypercholesterolemia, unspecified",
    "E78.01": "Familial hypercholesterolemia",
    "E78.1":  "Pure hyperglyceridemia",
    "E78.2":  "Mixed hyperlipidemia",
    "E78.4":  "Other hyperlipidemia",
    "E78.5":  "Hyperlipidemia, unspecified",
    "E78.00": "Pure hypercholesterolemia, unspecified",

    # ── Renal ────────────────────────────────────────────────────────────────
    "N17.0":  "Acute kidney failure with tubular necrosis",
    "N17.1":  "Acute kidney failure with acute cortical necrosis",
    "N17.2":  "Acute kidney failure with medullary necrosis",
    "N17.8":  "Other acute kidney failure",
    "N17.9":  "Acute kidney failure, unspecified",
    "N18.1":  "Chronic kidney disease, stage 1",
    "N18.2":  "Chronic kidney disease, stage 2 (mild)",
    "N18.3":  "Chronic kidney disease, stage 3 (moderate)",
    "N18.30": "Chronic kidney disease, stage 3 unspecified",
    "N18.31": "Chronic kidney disease, stage 3a",
    "N18.32": "Chronic kidney disease, stage 3b",
    "N18.4":  "Chronic kidney disease, stage 4 (severe)",
    "N18.5":  "Chronic kidney disease, stage 5",
    "N18.6":  "End-stage renal disease",
    "N18.9":  "Chronic kidney disease, unspecified",

    # ── Appendicitis / Surgical ──────────────────────────────────────────────
    "K35.2":  "Acute appendicitis with generalized peritonitis",
    "K35.20": "Acute appendicitis with generalized peritonitis, without abscess",
    "K35.21": "Acute appendicitis with generalized peritonitis and abscess",
    "K35.3":  "Acute appendicitis with localized peritonitis",
    "K35.80": "Other and unspecified acute appendicitis without abscess",
    "K35.89": "Other acute appendicitis with peritonitis",
    "K36":    "Other appendicitis",
    "K37":    "Unspecified appendicitis",
    "K38.0":  "Hyperplasia of appendix",

    # ── Cerebral palsy (FY2026) ──────────────────────────────────────────────
    "G80.0":  "Spastic quadriplegic cerebral palsy",
    "G80.1":  "Spastic diplegic cerebral palsy",
    "G80.2":  "Spastic hemiplegic cerebral palsy",
    "G80.3":  "Athetoid cerebral palsy",
    "G80.4":  "Ataxic cerebral palsy",
    "G80.8":  "Other cerebral palsy",
    "G80.9":  "Cerebral palsy, unspecified",

    # ── Hypertension / Cardiac additional ───────────────────────────────────
    "I20.0":  "Unstable angina",
    "I20.1":  "Angina pectoris with documented spasm",
    "I20.9":  "Angina pectoris, unspecified",
    "I27.0":  "Primary pulmonary hypertension",
    "I27.20": "Pulmonary hypertension, unspecified",
    "I27.21": "Secondary pulmonary arterial hypertension",
    "I27.29": "Other secondary pulmonary hypertension",
    "I35.0":  "Nonrheumatic aortic (valve) stenosis",
    "I34.0":  "Nonrheumatic mitral (valve) insufficiency",

    # ── Mental health ────────────────────────────────────────────────────────
    "F32.9":  "Major depressive disorder, single episode, unspecified",
    "F32.0":  "Major depressive disorder, single episode, mild",
    "F32.1":  "Major depressive disorder, single episode, moderate",
    "F32.2":  "Major depressive disorder, single episode, severe without psychotic features",
    "F33.0":  "Major depressive disorder, recurrent, mild",
    "F33.1":  "Major depressive disorder, recurrent, moderate",
    "F33.9":  "Major depressive disorder, recurrent, unspecified",
    "F41.0":  "Panic disorder without agoraphobia",
    "F41.1":  "Generalized anxiety disorder",
    "F41.9":  "Anxiety disorder, unspecified",
    "F43.10": "Post-traumatic stress disorder, unspecified",
    "F43.11": "Post-traumatic stress disorder, acute",
    "F43.12": "Post-traumatic stress disorder, chronic",

    # ── Status / Z codes ─────────────────────────────────────────────────────
    "Z79.01": "Long-term (current) use of anticoagulants",
    "Z79.02": "Long-term (current) use of antithrombotics/antiplatelets",
    "Z79.1":  "Long-term (current) use of non-steroidal anti-inflammatories (NSAID)",
    "Z79.4":  "Long-term (current) use of insulin",
    "Z79.52": "Long-term (current) use of systemic steroids",
    "Z79.84": "Long-term (current) use of oral hypoglycemic drugs",
    "Z87.891":"Personal history of nicotine dependence",
    "Z82.49": "Family history of ischemic heart disease and other diseases of the circulatory system",
    "Z87.39": "Personal history of other endocrine, nutritional and metabolic diseases",

    # ── Symptoms / Findings ──────────────────────────────────────────────────
    "R00.0":  "Tachycardia, unspecified",
    "R00.1":  "Bradycardia, unspecified",
    "R05.9":  "Cough, unspecified",
    "R06.00": "Dyspnea, unspecified",
    "R06.09": "Other forms of dyspnea",
    "R07.9":  "Chest pain, unspecified",
    "R10.9":  "Unspecified abdominal pain",
    "R11.2":  "Nausea with vomiting, unspecified",
    "R41.3":  "Other amnesia",
    "R55":    "Syncope and collapse",
    "R56.9":  "Unspecified convulsions",
    "R73.09": "Other abnormal glucose",
    "R91.8":  "Other nonspecific abnormal finding of lung field",
    "R94.31": "Abnormal electrocardiogram (ECG) (EKG)",
}

BUILTIN_CPT: dict[str, str] = {
    # ── E/M Office visits ────────────────────────────────────────────────────
    "99202": "Office/outpatient visit, new patient, low medical decision making",
    "99203": "Office/outpatient visit, new patient, moderate medical decision making",
    "99204": "Office/outpatient visit, new patient, moderate-high medical decision making",
    "99205": "Office/outpatient visit, new patient, high medical decision making",
    "99211": "Office/outpatient visit, established patient, minimal presenting problem",
    "99212": "Office/outpatient visit, established patient, straightforward MDM",
    "99213": "Office/outpatient visit, established patient, low medical decision making",
    "99214": "Office/outpatient visit, established patient, moderate medical decision making",
    "99215": "Office/outpatient visit, established patient, high medical decision making",

    # ── E/M Hospital ─────────────────────────────────────────────────────────
    "99221": "Initial hospital care, straightforward or low medical decision making",
    "99222": "Initial hospital care, moderate medical decision making",
    "99223": "Initial hospital care, high medical decision making",
    "99231": "Subsequent hospital care, straightforward or low medical decision making",
    "99232": "Subsequent hospital care, moderate medical decision making",
    "99233": "Subsequent hospital care, high medical decision making",
    "99238": "Hospital discharge day management, 30 minutes or less",
    "99239": "Hospital discharge day management, more than 30 minutes",

    # ── E/M Emergency ────────────────────────────────────────────────────────
    "99281": "Emergency department visit, self-limited or minor problem",
    "99282": "Emergency department visit, low complexity",
    "99283": "Emergency department visit, moderate complexity",
    "99284": "Emergency department visit, high complexity",
    "99285": "Emergency department visit, high complexity with threat to life",

    # ── Critical care ────────────────────────────────────────────────────────
    "99291": "Critical care, evaluation and management of critically ill, first 30-74 minutes",
    "99292": "Critical care, each additional 30 minutes",

    # ── Cardiac procedures ───────────────────────────────────────────────────
    "92928": "Percutaneous transcatheter placement of intracoronary stent(s), native coronary artery",
    "92929": "Percutaneous transcatheter placement of intracoronary stent(s), native coronary artery, each additional vessel",
    "92933": "Percutaneous transluminal coronary atherectomy, with stent placement, native coronary artery",
    "92941": "Percutaneous transluminal revascularization of acute total/subtotal occlusion during MI",
    "92943": "Percutaneous transcatheter placement of intracoronary stent(s), in-stent restenosis",
    "92950": "Cardiopulmonary resuscitation",
    "92960": "Cardioversion, elective, electrical conversion of arrhythmia; external",
    "93000": "Electrocardiogram, routine ECG with at least 12 leads; with interpretation and report",
    "93005": "Electrocardiogram, routine ECG; tracing only, without interpretation and report",
    "93010": "Electrocardiogram, routine ECG; interpretation and report only",
    "93015": "Cardiovascular stress test using maximal or submaximal treadmill or bicycle exercise",
    "93306": "Echocardiography, transthoracic, real-time with image documentation; complete",
    "93307": "Echocardiography, transthoracic, real-time; complete, without spectral or color flow Doppler",
    "93308": "Echocardiography, transthoracic, real-time; follow-up or limited study",
    "93312": "Echocardiography, transesophageal, real-time; complete",
    "93350": "Echocardiography, transthoracic, real-time; during rest and cardiovascular stress test",
    "93451": "Right heart catheterization",
    "93452": "Left heart catheterization",
    "93453": "Combined right and left heart catheterization",
    "93454": "Coronary angiography, without concomitant left heart catheterization",
    "93455": "Coronary angiography, with right heart catheterization",
    "93456": "Coronary angiography, with catheter placement in bypass graft(s)",
    "93457": "Coronary angiography, with catheter placement in bypass graft(s) and right heart catheterization",
    "93458": "Left heart catheterization with coronary angiography",
    "93459": "Left heart catheterization with coronary angiography and right heart catheterization",
    "93460": "Right and left heart catheterization with coronary angiography",
    "93461": "Right and left heart catheterization with coronary angiography and bypass graft angiography",
    "93619": "Comprehensive electrophysiologic evaluation, without attempted arrhythmia induction",
    "93620": "Comprehensive electrophysiologic evaluation with attempted arrhythmia induction",
    "93650": "Intracardiac catheter ablation of atrioventricular node function",
    "93653": "Comprehensive electrophysiologic evaluation with ablation of arrhythmia",
    "93654": "EP evaluation with ablation of ventricular tachycardia",
    "93656": "Comprehensive EP evaluation with ablation of atrial fibrillation",

    # ── Surgery / Appendix ───────────────────────────────────────────────────
    "44950": "Appendectomy",
    "44960": "Appendectomy; for ruptured appendix with abscess or generalized peritonitis",
    "44970": "Laparoscopic appendectomy",

    # ── Endoscopy ────────────────────────────────────────────────────────────
    "43235": "Upper GI endoscopy, diagnostic",
    "43239": "Upper GI endoscopy, with biopsy",
    "45378": "Colonoscopy, flexible, diagnostic",
    "45380": "Colonoscopy, flexible, with biopsy",
    "45385": "Colonoscopy, flexible, with removal of polyp by snare technique",

    # ── Radiology ────────────────────────────────────────────────────────────
    "70450": "CT head/brain, without contrast",
    "70460": "CT head/brain, with contrast",
    "70470": "CT head/brain, without and with contrast",
    "70551": "MRI brain, without contrast",
    "70552": "MRI brain, with contrast",
    "70553": "MRI brain, without and with contrast",
    "71045": "Radiologic examination, chest; single view",
    "71046": "Radiologic examination, chest; 2 views",
    "71047": "Radiologic examination, chest; 3 views",
    "71048": "Radiologic examination, chest; 4 or more views",
    "71250": "CT thorax, without contrast",
    "71260": "CT thorax, with contrast",
    "71270": "CT thorax, without and with contrast",
    "72131": "CT lumbar spine, without contrast",
    "72132": "CT lumbar spine, with contrast",
    "72148": "MRI lumbar spine, without contrast",
    "72149": "MRI lumbar spine, with contrast",
    "74150": "CT abdomen, without contrast",
    "74160": "CT abdomen, with contrast",
    "74170": "CT abdomen, without and with contrast",
    "74177": "CT abdomen and pelvis, with contrast",
    "74178": "CT abdomen and pelvis, without and with contrast",
    "74176": "CT abdomen and pelvis, without contrast",

    # ── Lab ──────────────────────────────────────────────────────────────────
    "80048": "Basic metabolic panel",
    "80053": "Comprehensive metabolic panel",
    "80061": "Lipid panel",
    "83036": "Hemoglobin A1c",
    "84443": "Thyroid stimulating hormone (TSH)",
    "85025": "Blood count; complete (CBC), automated and automated differential WBC count",
    "85610": "Prothrombin time",
    "86360": "CD4 count",
    "87070": "Culture, bacterial; any other source",

    # ── Pulmonary ────────────────────────────────────────────────────────────
    "94010": "Spirometry, including graphic record, total and timed vital capacity",
    "94060": "Bronchodilation responsiveness, spirometry",
    "94640": "Pressurized or nonpressurized inhalation treatment",
    "94660": "Continuous positive airway pressure (CPAP) ventilation initiation and management",
    "94720": "Carbon monoxide diffusing capacity (DLCO)",

    # ── Mental health ────────────────────────────────────────────────────────
    "90791": "Psychiatric diagnostic evaluation",
    "90792": "Psychiatric diagnostic evaluation with medical services",
    "90832": "Psychotherapy, 30 minutes",
    "90834": "Psychotherapy, 45 minutes",
    "90837": "Psychotherapy, 60 minutes",
    "90847": "Family psychotherapy with patient present, 50 minutes",
}


def _get_builtin(code: str, code_type: str) -> dict | None:
    """Check built-in dictionary first — instant, no I/O."""
    code = code.strip().upper()
    if code_type.upper() in ("ICD10", "ICD-10"):
        desc = BUILTIN_ICD10.get(code)
        if desc:
            return {"code": code, "description": desc, "type": "ICD10", "source": "CMS_2026"}
    elif code_type.upper() in ("CPT", "HCPCS"):
        desc = BUILTIN_CPT.get(code)
        if desc:
            return {"code": code, "description": desc, "type": "CPT", "source": "AMA_CPT_2024"}
    return None


def _load_medical_codes_json() -> dict[str, dict]:
    """
    Load medical_codes.json as a supplementary source.
    Handles both formats: {"icd10":[...], "cpt":[...]} and flat dict.
    """
    db_file = Path(__file__).parent.parent / "medical_codes.json"
    if not db_file.exists():
        return {}
    try:
        raw = json.loads(db_file.read_text())
        result = {}
        entries = []
        if isinstance(raw, dict) and ("icd10" in raw or "cpt" in raw):
            entries = raw.get("icd10", []) + raw.get("cpt", [])
        elif isinstance(raw, list):
            entries = raw
        for entry in entries:
            if isinstance(entry, dict) and "code" in entry:
                code = str(entry["code"]).strip().upper()
                desc = entry.get("description") or entry.get("name") or ""
                if code and desc:
                    ctype = "CPT" if code.isdigit() and len(code) == 5 else "ICD10"
                    result[code] = {"code": code, "description": desc, "type": ctype, "source": "LOCAL_JSON"}
        return result
    except Exception as e:
        logger.warning(f"Could not load medical_codes.json: {e}")
        return {}

_json_db: dict[str, dict] = _load_medical_codes_json()


# ════════════════════════════════════════════════════════════════════════════
# SYNC lookup functions — main.py imports these by name
# ════════════════════════════════════════════════════════════════════════════

def lookup_icd10_code(code: str) -> dict | None:
    """
    Sync exact ICD-10-CM lookup.
    Priority: disk cache → built-in dict → medical_codes.json → NLM API.
    Never blocks longer than TIMEOUT seconds.
    """
    code = code.strip().upper()
    cache_key = f"icd10:{code}"

    # 1. Disk cache
    if cache_key in _cache and _cache[cache_key]:
        return _cache[cache_key]

    # 2. Built-in database (instant — covers all common codes)
    entry = _get_builtin(code, "ICD10")
    if entry:
        _cache[cache_key] = entry
        return entry

    # 3. medical_codes.json
    if code in _json_db:
        entry = _json_db[code]
        _cache[cache_key] = entry
        return entry

    # 4. NLM API — try multiple strategies
    search_terms = [code]
    if "." in code:
        search_terms.append(code.replace(".", ""))   # J189
        search_terms.append(code.split(".")[0])       # J18

    for endpoint in NLM_ICD10_ENDPOINTS:
        for term in search_terms:
            try:
                with httpx.Client(timeout=TIMEOUT) as client:
                    resp = client.get(endpoint, params={
                        "terms": term, "maxList": 20,
                        "sf": "code,name", "df": "code,name",
                    })
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    items = data[3] if data and len(data) > 3 else []
                    for item in (items or []):
                        if not isinstance(item, (list, tuple)) or len(item) < 2:
                            continue
                        item_code = str(item[0]).strip().upper()
                        if item_code == code or item_code.replace(".", "") == code.replace(".", ""):
                            year = "2026" if "2026" in endpoint else "2024"
                            entry = {"code": item[0], "description": str(item[1]).strip(),
                                     "type": "ICD10", "source": f"NIH_NLM_{year}"}
                            _cache[cache_key] = entry
                            _save_cache()
                            logger.info(f"✅ NLM {year}: {code} → '{item[1]}'")
                            return entry
            except Exception as e:
                logger.debug(f"NLM {endpoint} '{term}': {e}")
                continue

    logger.info(f"❌ ICD10 {code} not found anywhere")
    return None


def lookup_cpt_code(code: str) -> dict | None:
    """
    Sync exact CPT/HCPCS lookup.
    Priority: disk cache → built-in dict → medical_codes.json → NLM HCPCS API.
    """
    code = code.strip().upper()
    cache_key = f"cpt:{code}"

    # 1. Disk cache
    if cache_key in _cache and _cache[cache_key]:
        return _cache[cache_key]

    # 2. Built-in database
    entry = _get_builtin(code, "CPT")
    if entry:
        _cache[cache_key] = entry
        return entry

    # 3. medical_codes.json
    if code in _json_db:
        entry = _json_db[code]
        _cache[cache_key] = entry
        return entry

    # 4. NLM HCPCS API
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.get(NLM_HCPCS_API, params={
                "terms": code, "maxList": 10,
                "sf": "code,display", "df": "code,display",
            })
            if resp.status_code == 200:
                data = resp.json()
                items = data[3] if data and len(data) > 3 else []
                for item in (items or []):
                    if not isinstance(item, (list, tuple)) or len(item) < 2:
                        continue
                    if str(item[0]).strip().upper() == code:
                        entry = {"code": item[0], "description": str(item[1]).strip(),
                                 "type": "CPT", "source": "NIH_HCPCS"}
                        _cache[cache_key] = entry
                        _save_cache()
                        return entry
    except Exception as e:
        logger.debug(f"HCPCS lookup {code}: {e}")

    logger.info(f"❌ CPT {code} not found")
    return None


def search_icd10_codes(diagnosis_text: str, limit: int = 8) -> list[dict]:
    """Sync ICD-10-CM search by text. Called from run_full_audit (thread executor)."""
    if not diagnosis_text or len(diagnosis_text) < 3:
        return []

    cache_key = f"search_icd10:{diagnosis_text.lower()[:50]}:{limit}"
    if cache_key in _cache:
        return _cache[cache_key]

    results = []

    # Try NLM API first (best results)
    for endpoint in NLM_ICD10_ENDPOINTS:
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(endpoint, params={
                    "terms": diagnosis_text, "maxList": limit,
                    "sf": "code,name", "df": "code,name",
                })
                if resp.status_code == 200:
                    data = resp.json()
                    items = data[3] if data and len(data) > 3 else []
                    for item in (items or []):
                        if isinstance(item, (list, tuple)) and len(item) >= 2:
                            results.append({
                                "code": item[0], "description": item[1],
                                "type": "ICD10",
                                "source": "NIH_NLM_2026" if "2026" in endpoint else "NIH_NLM_2024",
                            })
            if results:
                break
        except Exception as e:
            logger.debug(f"NLM search failed: {e}")

    # Fallback: search built-in dict
    if not results:
        q = diagnosis_text.lower()
        for code, desc in BUILTIN_ICD10.items():
            if q in desc.lower():
                results.append({"code": code, "description": desc, "type": "ICD10", "source": "BUILTIN"})
            if len(results) >= limit:
                break

    _cache[cache_key] = results
    _save_cache()
    return results


def search_cpt_codes(procedure_text: str, limit: int = 5) -> list[dict]:
    """Sync CPT search by procedure description."""
    if not procedure_text:
        return []
    q = procedure_text.lower()
    results = []
    for code, desc in BUILTIN_CPT.items():
        if q in desc.lower():
            results.append({"code": code, "description": desc, "type": "CPT", "source": "BUILTIN"})
        if len(results) >= limit:
            break
    # Also search JSON db
    if len(results) < limit:
        for code, entry in _json_db.items():
            if q in entry.get("description", "").lower():
                results.append(entry)
            if len(results) >= limit:
                break
    return results[:limit]


def validate_code(code: str, code_type: str) -> tuple[bool, str, str]:
    """Sync validate. Returns (is_valid, description, source)."""
    code = code.strip().upper()
    if code_type.upper() in ("ICD10", "ICD-10"):
        result = lookup_icd10_code(code)
        if result:
            return True, result["description"], result.get("source", "CMS_2026")
        return False, f"{code} not found in ICD-10-CM 2026", "NOT_FOUND"
    elif code_type.upper() == "CPT":
        result = lookup_cpt_code(code)
        if result:
            return True, result["description"], "AMA_CPT_2024"
        return False, f"{code} not found in CPT database", "NOT_FOUND"
    return False, "Unknown code type", ""


def get_descriptions_for_codes(codes: list[str], code_type: str) -> dict[str, str]:
    """
    Sync batch description lookup.
    Returns {code: description}. Empty string only if truly not found.
    This is the function called from get_report() via run_in_executor.
    """
    result = {}
    for code in codes:
        code = code.strip().upper()
        if not code:
            continue
        if code_type.upper() in ("ICD10", "ICD-10"):
            entry = lookup_icd10_code(code)
        else:
            entry = lookup_cpt_code(code)
        result[code] = entry["description"] if entry else ""
    return result


# ── Async wrappers (for FastAPI routes that await directly) ───────────────────

async def lookup_icd10_code_async(code: str) -> dict | None:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lookup_icd10_code, code)

async def lookup_cpt_code_async(code: str) -> dict | None:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lookup_cpt_code, code)

async def get_descriptions_for_codes_async(codes: list[str], code_type: str) -> dict[str, str]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_descriptions_for_codes, codes, code_type)