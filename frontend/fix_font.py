with open('src/pages/AlphaFlowPage.tsx','r',encoding='utf-8') as f: c = f.read()
old = "fontSize:10,color:'#6e7a8a',lineHeight:1.6,marginBottom:6"
new = "fontSize:12,color:'#6e7a8a',lineHeight:1.7,marginBottom:8"
print('old in content:', old in c)
c = c.replace(old, new)
print('remaining fontSize:10:', c.count('fontSize:10'))
with open('src/pages/AlphaFlowPage.tsx','w',encoding='utf-8') as f: f.write(c)
