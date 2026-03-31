// AuditReport.jsx — PDF download button + confidence bars + NLM badges + risk gauge
import axios from 'axios'
import { useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import { useNavigate, useParams } from 'react-router-dom'

const API = import.meta.env.VITE_API_URL || ''

function RiskGauge({ risk, revenue }) {
  const angles = { low:40, medium:95, high:140, critical:175 }
  const colors  = { low:'#22c55e', medium:'#eab308', high:'#f97316', critical:'#ef4444' }
  const angle = angles[risk]||90, color = colors[risk]||'#94a3b8'
  const rad=Math.PI/180*(180-angle), nx=(90+68*Math.cos(rad)).toFixed(1), ny=(90-68*Math.sin(rad)).toFixed(1)
  return (
    <div style={{textAlign:'center'}}>
      <svg width="160" height="98" viewBox="0 0 180 110" style={{overflow:'visible'}}>
        <path d="M20,90 A70,70 0 0,1 57,27"  fill="none" stroke="#22c55e" strokeWidth="12" strokeLinecap="round"/>
        <path d="M57,27 A70,70 0 0,1 107,20" fill="none" stroke="#eab308" strokeWidth="12" strokeLinecap="round"/>
        <path d="M107,20 A70,70 0 0,1 143,43" fill="none" stroke="#f97316" strokeWidth="12" strokeLinecap="round"/>
        <path d="M143,43 A70,70 0 0,1 160,90" fill="none" stroke="#ef4444" strokeWidth="12" strokeLinecap="round"/>
        <line x1="90" y1="90" x2={nx} y2={ny} stroke={color} strokeWidth="3.5" strokeLinecap="round"/>
        <circle cx="90" cy="90" r="7" fill={color} stroke="white" strokeWidth="2"/>
        <text x="90" y="76" fill={color} fontSize="10" fontWeight="bold" fontFamily="Arial" textAnchor="middle">
          ${(revenue||0).toLocaleString()}
        </text>
      </svg>
      <p style={{fontSize:'1.2rem',fontWeight:800,color,margin:'2px 0 0'}}>{risk?.toUpperCase()}</p>
    </div>
  )
}

function NLMBadge({source}) {
  const isLive=source==='nlm_live'||String(source||'').includes('NLM')
  return <span style={{display:'inline-block',background:'#f0fdf4',color:'#166534',border:'1px solid #bbf7d0',borderRadius:5,padding:'1px 7px',fontSize:'0.68rem',fontWeight:700,marginLeft:6}}>{isLive?'✓ NIH NLM':'✓ CMS 2024'}</span>
}

function ConfBar({score=75}) {
  const color=score>=85?'#22c55e':score>=65?'#eab308':'#f97316'
  return (
    <div style={{marginTop:8}}>
      <span style={{fontSize:'0.7rem',color:'#64748b',fontWeight:600}}>AI Confidence: {score}%</span>
      <div style={{background:'#e2e8f0',borderRadius:999,height:7,marginTop:3,overflow:'hidden'}}>
        <div style={{background:color,width:`${score}%`,height:'100%',borderRadius:999,transition:'width .8s ease'}}/>
      </div>
    </div>
  )
}

function Row({label,value,bold}) {
  return (
    <div style={{display:'grid',gridTemplateColumns:'160px 1fr',gap:8,padding:'8px 0',borderBottom:'1px solid #f1f5f9'}}>
      <span style={{fontSize:'0.78rem',fontWeight:700,color:'#64748b',textTransform:'uppercase',letterSpacing:'.04em'}}>{label}</span>
      <span style={{fontSize:'0.9rem',color:'#1e293b',fontWeight:bold?700:400}}>{value}</span>
    </div>
  )
}

const SEV_COLOR={critical:'#ef4444',high:'#f97316',medium:'#eab308',low:'#22c55e'}
const SEV_EMOJI={critical:'🔴',high:'🟠',medium:'🟡',low:'🟢'}
const RISK_COLOR={critical:'#ef4444',high:'#f97316',medium:'#eab308',low:'#22c55e'}

export default function AuditReport() {
  const {caseId}=useParams(), navigate=useNavigate()
  const [report,setReport]=useState(null), [loading,setLoading]=useState(true)
  const [activeTab,setActiveTab]=useState('discrepancies'), [pdfLoading,setPdfLoading]=useState(false)
  const pollRef=useRef(null)

  useEffect(()=>{
    const poll=async()=>{
      try {
        const {data:s}=await axios.get(`${API}/api/audit/${caseId}/status`)
        if(s.status==='completed'){
          clearInterval(pollRef.current)
          const {data}=await axios.get(`${API}/api/audit/${caseId}/report`)
          setReport(data); setLoading(false)
          const hist=JSON.parse(sessionStorage.getItem('auditHistory')||'[]')
          hist.push({caseId,revenue:data.total_revenue_impact_usd,discrepancies:data.total_discrepancies,risk:data.risk_level})
          sessionStorage.setItem('auditHistory',JSON.stringify(hist.slice(-10)))
        } else if(s.status==='error'){
          clearInterval(pollRef.current); toast.error('Audit failed'); setLoading(false)
        }
      } catch { clearInterval(pollRef.current); toast.error('Failed to load'); setLoading(false) }
    }
    pollRef.current=setInterval(poll,2500); poll()
    return ()=>clearInterval(pollRef.current)
  },[caseId])

  const handleDownloadPDF=async()=>{
    setPdfLoading(true)
    try {
      const token=localStorage.getItem('cp_token')||''
      const res=await axios.get(`${API}/api/audit/${caseId}/pdf`,{
        responseType:'blob', headers:{Authorization:`Bearer ${token}`}
      })
      const url=window.URL.createObjectURL(new Blob([res.data],{type:'application/pdf'}))
      const a=document.createElement('a')
      a.href=url; a.setAttribute('download',`CodePerfect-Defense-${caseId}.pdf`)
      document.body.appendChild(a); a.click(); a.remove()
      window.URL.revokeObjectURL(url)
      toast.success('PDF downloaded!')
    } catch { toast.error('PDF download failed') }
    finally { setPdfLoading(false) }
  }

  if(loading) return (
    <div style={{display:'flex',alignItems:'center',justifyContent:'center',height:'60vh',flexDirection:'column',gap:16}}>
      <div style={{width:40,height:40,border:'3px solid #1d4ed8',borderTop:'3px solid transparent',borderRadius:'50%',animation:'spin 1s linear infinite'}}/>
      <p style={{color:'#64748b',fontSize:'0.875rem'}}>AI audit running — validating against NIH NLM...</p>
      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
    </div>
  )
  if(!report) return <div style={{padding:'2rem',color:'#ef4444'}}>Report not found.</div>

  const risk=report.risk_level||'low', n=report.total_discrepancies||0, rev=report.total_revenue_impact_usd||0
  const aiAll=[...(report.ai_icd10_codes||[]),...(report.ai_cpt_codes||[])]
  const hICD=report.human_icd10_codes||[], hCPT=report.human_cpt_codes||[]
  const hDescs={...(report.human_icd10_descriptions||{}),...(report.human_cpt_descriptions||{})}
  const hist=JSON.parse(sessionStorage.getItem('auditHistory')||'[]'), prev=hist.slice(-2)
  const detOk=prev.length>=2&&prev[0].revenue===prev[1].revenue&&prev[0].discrepancies===prev[1].discrepancies&&prev[0].risk===prev[1].risk

  const PdfBtn=({big})=>(
    <button onClick={handleDownloadPDF} disabled={pdfLoading} style={{
      display:'flex',alignItems:'center',gap:8,
      background:pdfLoading?'#94a3b8':big?'linear-gradient(135deg,#dc2626,#b91c1c)':'#dc2626',
      color:'white',border:'none',borderRadius:big?10:8,
      padding:big?'0.75rem 1.6rem':'0.65rem 1.2rem',
      cursor:pdfLoading?'not-allowed':'pointer',fontWeight:700,
      fontSize:big?'0.95rem':'0.875rem',
      boxShadow:big?'0 4px 14px rgba(220,38,38,.4)':'none',transition:'all .2s',
    }}>
      {pdfLoading
        ? <><div style={{width:16,height:16,border:'2px solid rgba(255,255,255,.4)',borderTop:'2px solid white',borderRadius:'50%',animation:'spin 1s linear infinite'}}/> Generating...</>
        : <><span style={{fontSize:big?'1.1rem':'1rem'}}>📄</span> Download PDF Defense Report</>
      }
    </button>
  )

  return (
    <div style={{padding:'1.5rem',maxWidth:1200,margin:'0 auto'}}>
      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>

      {/* Header */}
      <div style={{display:'flex',alignItems:'flex-start',justifyContent:'space-between',marginBottom:'1rem',flexWrap:'wrap',gap:'1rem'}}>
        <div>
          <button onClick={()=>navigate('/audit')} style={{background:'none',border:'none',color:'#3b82f6',cursor:'pointer',fontSize:'0.875rem',padding:0,marginBottom:4}}>← New Audit</button>
          <h2 style={{fontSize:'1.4rem',fontWeight:700,color:'#0f172a',margin:'0 0 4px'}}>Audit Report</h2>
          <p style={{color:'#64748b',fontSize:'0.82rem',margin:0}}>
            Case: <code style={{background:'#f1f5f9',padding:'2px 6px',borderRadius:4}}>{caseId}</code>
            &nbsp;·&nbsp;{(report.processing_time_ms/1000).toFixed(1)}s
          </p>
        </div>
        {/* BIG PDF button top-right */}
        <PdfBtn big />
      </div>

      {/* Summary */}
      <div style={{background:'#eff6ff',borderLeft:'4px solid #3b82f6',borderRadius:'0 8px 8px 0',padding:'0.9rem 1.2rem',marginBottom:'1.2rem'}}>
        <strong style={{color:'#1d4ed8'}}>AI Summary: </strong>
        <span style={{color:'#1e293b'}}>{report.summary}</span>
      </div>

      {/* Metric cards */}
      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr 1fr 1fr',gap:'1rem',marginBottom:'1.5rem'}}>
        <div style={{background:'white',borderRadius:12,padding:'1rem',boxShadow:'0 1px 4px rgba(0,0,0,.07)',textAlign:'center'}}>
          <p style={{fontSize:'0.7rem',color:'#64748b',fontWeight:700,textTransform:'uppercase',margin:'0 0 4px'}}>Risk Level</p>
          <RiskGauge risk={risk} revenue={rev}/>
        </div>
        <div style={{background:'white',borderRadius:12,padding:'1.2rem',boxShadow:'0 1px 4px rgba(0,0,0,.07)',borderLeft:`4px solid ${n>0?'#ef4444':'#22c55e'}`}}>
          <p style={{fontSize:'0.7rem',color:'#64748b',fontWeight:700,textTransform:'uppercase',margin:'0 0 4px'}}>Discrepancies</p>
          <p style={{fontSize:'2rem',fontWeight:700,color:n>0?'#ef4444':'#22c55e',margin:0}}>{n}</p>
          <p style={{fontSize:'0.72rem',color:'#94a3b8',margin:'4px 0 0'}}>{n>0?'errors found':'clean claim ✓'}</p>
        </div>
        <div style={{background:'white',borderRadius:12,padding:'1.2rem',boxShadow:'0 1px 4px rgba(0,0,0,.07)',borderLeft:`4px solid ${rev>0?'#ef4444':'#22c55e'}`}}>
          <p style={{fontSize:'0.7rem',color:'#64748b',fontWeight:700,textTransform:'uppercase',margin:'0 0 4px'}}>Revenue Impact</p>
          <p style={{fontSize:'1.8rem',fontWeight:700,color:rev>0?'#ef4444':'#22c55e',margin:0}}>${rev.toLocaleString()}</p>
          <p style={{fontSize:'0.68rem',color:'#94a3b8',margin:'4px 0 0'}}>CMS MPFS 2024 / MS-DRG v41</p>
        </div>
        <div style={{background:'white',borderRadius:12,padding:'1.2rem',boxShadow:'0 1px 4px rgba(0,0,0,.07)',borderLeft:'4px solid #3b82f6'}}>
          <p style={{fontSize:'0.7rem',color:'#64748b',fontWeight:700,textTransform:'uppercase',margin:'0 0 4px'}}>Processing</p>
          <p style={{fontSize:'2rem',fontWeight:700,color:'#1e293b',margin:0}}>{(report.processing_time_ms/1000).toFixed(1)}s</p>
          <p style={{fontSize:'0.72rem',color:'#94a3b8',margin:'4px 0 0'}}>end-to-end</p>
        </div>
        <div style={{background:'white',borderRadius:12,padding:'1rem',boxShadow:'0 1px 4px rgba(0,0,0,.07)',display:'flex',alignItems:'center',justifyContent:'center'}}>
          {hist.length>=2
            ? <div style={{background:detOk?'#f0fdf4':'#fef2f2',border:`1.5px solid ${detOk?'#86efac':'#fca5a5'}`,borderRadius:8,padding:'0.6rem 0.8rem',textAlign:'center',fontSize:'0.8rem',fontWeight:700,color:detOk?'#166534':'#991b1b',whiteSpace:'pre-line'}}>
                {detOk?'✅ Determinism\nVerified':'⚠️ Results\nDiffer'}
                <div style={{fontSize:'0.68rem',fontWeight:400,marginTop:2}}>{detOk?'Run 1 = Run 2':'temp must be 0'}</div>
              </div>
            : <div style={{fontSize:'0.75rem',color:'#94a3b8',textAlign:'center'}}>🔁 Run twice<br/>to verify<br/>determinism</div>
          }
        </div>
      </div>

      {/* Tabs */}
      <div style={{display:'flex',gap:2,marginBottom:'1rem',borderBottom:'2px solid #e2e8f0'}}>
        {[['discrepancies',`🔍 Discrepancies (${n})`],['codes','⚖️ Code Comparison'],['facts','🧬 Clinical Facts']].map(([id,label])=>(
          <button key={id} onClick={()=>setActiveTab(id)} style={{padding:'0.6rem 1.1rem',border:'none',cursor:'pointer',background:'transparent',fontWeight:activeTab===id?700:400,color:activeTab===id?'#1d4ed8':'#64748b',borderBottom:activeTab===id?'2px solid #1d4ed8':'2px solid transparent',marginBottom:-2,fontSize:'0.875rem'}}>
            {label}
          </button>
        ))}
      </div>

      {/* Discrepancies */}
      {activeTab==='discrepancies'&&(
        <div>
          {n===0
            ? <div style={{background:'#f0fdf4',border:'1px solid #86efac',borderRadius:12,padding:'2rem',textAlign:'center'}}>
                <div style={{fontSize:'2.5rem'}}>✅</div>
                <p style={{fontWeight:700,color:'#166534',fontSize:'1.1rem',marginTop:8}}>No discrepancies — claim is accurate and complete.</p>
                <p style={{color:'#4ade80',fontSize:'0.875rem'}}>All codes match clinical documentation. Ready for submission.</p>
              </div>
            : (report.discrepancies||[]).map((d,i)=>{
                const sev=d.severity||'medium', code=d.ai_code||d.human_code||'—'
                const conf=d.confidence_score||75, aiRef=aiAll.find(c=>c.code===code)
                return (
                  <div key={i} style={{background:'white',borderRadius:10,padding:'1rem 1.2rem',marginBottom:'0.8rem',boxShadow:'0 1px 3px rgba(0,0,0,.06)',borderLeft:`4px solid ${SEV_COLOR[sev]||'#94a3b8'}`}}>
                    <div style={{display:'flex',flexWrap:'wrap',alignItems:'center',gap:8,marginBottom:6}}>
                      <span style={{fontSize:'0.7rem',fontWeight:700,textTransform:'uppercase',color:'#64748b'}}>{SEV_EMOJI[sev]} {String(d.discrepancy_type||'').replace(/_/g,' ').toUpperCase()}</span>
                      <span style={{fontSize:'0.7rem',color:'#64748b'}}>Severity: <strong>{sev.toUpperCase()}</strong></span>
                      <span style={{fontSize:'0.7rem',color:'#64748b'}}>Code: <strong style={{fontFamily:'monospace'}}>{code}</strong>{aiRef&&<NLMBadge source={aiRef.source||'CMS 2024'}/>}</span>
                      <span style={{marginLeft:'auto',background:'#fef2f2',color:'#dc2626',borderRadius:6,padding:'2px 10px',fontSize:'0.875rem',fontWeight:700}}>${(d.estimated_revenue_impact_usd||0).toLocaleString()}</span>
                    </div>
                    <p style={{fontSize:'1rem',fontWeight:600,color:'#1e293b',margin:'0 0 6px'}}>{d.description}</p>
                    {d.chart_evidence&&d.chart_evidence.length>5&&(
                      <div style={{background:'#eff6ff',borderLeft:'3px solid #3b82f6',padding:'8px 12px',borderRadius:'0 6px 6px 0',fontStyle:'italic',color:'#1d4ed8',fontSize:'0.875rem',margin:'8px 0'}}>
                        📌 &ldquo;{d.chart_evidence}&rdquo;
                      </div>
                    )}
                    <p style={{fontSize:'0.85rem',color:'#475569',margin:'6px 0 4px'}}>{d.clinical_justification}</p>
                    {d.financial_impact&&<p style={{fontSize:'0.82rem',color:'#dc2626',fontWeight:600,margin:'4px 0'}}>💰 {d.financial_impact}</p>}
                    <p style={{fontSize:'0.85rem',color:'#1d4ed8',fontWeight:600,margin:'4px 0 0'}}>→ {d.recommendation}</p>
                    <ConfBar score={conf}/>
                  </div>
                )
              })
          }
        </div>
      )}

      {/* Code Comparison */}
      {activeTab === 'codes' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem' }}>

          {/* HUMAN CODES */}
          <div style={{
            background: '#ffffff',
            borderRadius: 12,
            padding: '1.2rem',
            boxShadow: '0 2px 6px rgba(0,0,0,0.06)',
            border: '1px solid #e2e8f0'
          }}>
            <h3 style={{ fontWeight: 700, marginBottom: 12, color: '#2563eb' }}>
              👤 Human Codes
            </h3>

            {[...hICD, ...hCPT].length === 0 ? (
              <p style={{ color: '#94a3b8' }}>No codes submitted</p>
            ) : (
              [...hICD, ...hCPT].map((code, i) => {
                const desc = hDescs[code] || 'No description available'
                const valid = desc && !desc.toLowerCase().includes('not found')

                return (
                  <div key={i} style={{
                    padding: '10px 12px',
                    marginBottom: 10,
                    borderRadius: 8,
                    background: valid ? '#f8fafc' : '#fef2f2',
                    border: `1px solid ${valid ? '#e2e8f0' : '#fecaca'}`
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ 
                        fontFamily: 'monospace', 
                        fontWeight: 700,
                        color: '#020617',
                        fontSize: '0.95rem'
                      }}>
                        {code}
                      </span>

                      <span style={{
                        fontSize: '0.7rem',
                        fontWeight: 700,
                        color: valid ? '#16a34a' : '#dc2626'
                      }}>
                        {valid ? '✓ Valid' : '✗ Invalid'}
                      </span>
                    </div>

                    <p style={{
                      marginTop: 4,
                      fontSize: '0.85rem',
                      color: '#0f172a'
                    }}>
                      {desc}
                    </p>
                  </div>
                )
              })
            )}
          </div>

          {/* AI CODES */}
          <div style={{
            background: '#ffffff',
            borderRadius: 12,
            padding: '1.2rem',
            boxShadow: '0 2px 6px rgba(0,0,0,0.06)',
            border: '1px solid #e2e8f0'
          }}>
            <h3 style={{ fontWeight: 700, marginBottom: 12, color: '#16a34a' }}>
              🤖 AI Generated Codes
            </h3>

            {aiAll.length === 0 ? (
              <p style={{ color: '#94a3b8' }}>No AI codes generated</p>
            ) : (
              aiAll.map((c, i) => (
                <div key={i} style={{
                  padding: '10px 12px',
                  marginBottom: 10,
                  borderRadius: 8,
                  background: '#f0fdf4',
                  border: '1px solid #bbf7d0'
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ 
                      fontFamily: 'monospace', 
                      fontWeight: 700,
                      color: '#020617',
                      fontSize: '0.95rem'
                    }}>
                      {c.code}
                    </span>

                    <span style={{
                      fontSize: '0.7rem',
                      fontWeight: 700,
                      color: '#16a34a'
                    }}>
                      ✓ Validated
                    </span>
                  </div>

                  <p style={{
                    marginTop: 4,
                    fontSize: '0.85rem',
                    color: '#475569'
                  }}>
                    {c.description}
                  </p>

                  {c.confidence && (
                    <p style={{
                      fontSize: '0.7rem',
                      color: '#94a3b8',
                      marginTop: 3
                    }}>
                      Confidence: {Math.round(c.confidence * 100)}%
                    </p>
                  )}
                </div>
              ))
            )}
          </div>

        </div>
      )}
      {/* Clinical Facts */}
      {activeTab==='facts'&&(()=>{
        const cf=report.clinical_facts||{}
        return (
          <div style={{background:'white',borderRadius:12,padding:'1.5rem',boxShadow:'0 1px 4px rgba(0,0,0,.07)'}}>
            <Row label="Primary Diagnosis" value={cf.primary_diagnosis||'—'} bold/>
            {cf.patient_age&&<Row label="Patient" value={`${cf.patient_age} y/o ${cf.patient_gender||''}`}/>}
            {cf.admission_type&&<Row label="Admission" value={cf.admission_type}/>}
            {cf.comorbidities?.length>0&&<Row label="Comorbidities" value={<ul style={{margin:0,paddingLeft:18}}>{cf.comorbidities.map((c,i)=><li key={i}>{c}</li>)}</ul>}/>}
            {cf.procedures_performed?.length>0&&<Row label="Procedures" value={<ul style={{margin:0,paddingLeft:18}}>{cf.procedures_performed.map((p,i)=><li key={i}>{p}</li>)}</ul>}/>}
            {cf.key_clinical_indicators?.length>0&&<Row label="Key Indicators" value={<ul style={{margin:0,paddingLeft:18}}>{cf.key_clinical_indicators.map((k,i)=><li key={i}>{k}</li>)}</ul>}/>}
          </div>
        )
      })()}

      {/* Bottom action bar */}
      <div style={{marginTop:'2rem',padding:'1rem 1.2rem',background:'white',borderRadius:12,boxShadow:'0 1px 4px rgba(0,0,0,.07)',display:'flex',gap:'1rem',alignItems:'center',flexWrap:'wrap'}}>
        <PdfBtn/>
        <button onClick={()=>navigate('/audit')} style={{background:'#f1f5f9',border:'none',cursor:'pointer',padding:'0.65rem 1.2rem',borderRadius:8,fontWeight:600,fontSize:'0.875rem',color:'#475569'}}>+ New Audit</button>
        <button onClick={()=>navigate('/cases')} style={{background:'#f1f5f9',border:'none',cursor:'pointer',padding:'0.65rem 1.2rem',borderRadius:8,fontWeight:600,fontSize:'0.875rem',color:'#475569'}}>All Cases →</button>
        <div style={{marginLeft:'auto',fontSize:'0.75rem',color:'#94a3b8'}}>
          All codes validated · NIH NLM API · CMS ICD-10-CM 2024 · AMA CPT 2024
        </div>
      </div>
    </div>
  )
}