/** Global Design Tokens — Bloomberg Terminal style (v4.7). */

export const T = {
  c: {
    pageBg:'#0b0e14', cardBg:'#111620', panelBg:'#161b27', border:'#1e2535',
    text:'#c9d1d9', textDim:'#6e7a8a', textMuted:'#4b5563', body:'#8b949e',
    code:'#06b6d4', purple:'#a78bfa', blue:'#3b82f6', violet:'#8b5cf6',
    green:'#10b981', red:'#ef4444', amber:'#f59e0b', orange:'#f97316',
    darkRed:'#7f1d1d', white:'#fff', gray:'#374151',
  },
  font: {
    dataLg:{fontSize:22,fontWeight:700,color:'#e5e7eb'},
    dataMd:{fontSize:15,fontWeight:600,color:'#d1d5db'},
    dataSm:{fontSize:13,fontWeight:600,color:'#c9d1d9'},
    label:{fontSize:11,fontWeight:500,color:'#6e7a8a'},
    caption:{fontSize:10,fontWeight:400,color:'#4b5563'},
  },
  space:{xs:4,sm:8,md:12,lg:16,xl:24},
  radius:{card:6,badge:3,btn:6,panel:8},
  card:{bg:'#111620',border:'1px solid #1e2535',hoverBg:'#161b27'},
  panel:{bg:'#161b27',border:'1px solid #1e2535'},
  table:{rowH:44,headerH:38},
  page:{maxW:1600,bg:'#0b0e14',color:'#c9d1d9',font:'system-ui'},
} as const;

export const TH = {padding:'6px 10px',textAlign:'left' as const,fontSize:12,color:'#6e7a8a',fontWeight:500};
export const TD = {padding:'5px 10px',fontSize:13};
