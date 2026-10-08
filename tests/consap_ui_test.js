const fs=require('fs'),vm=require('vm'),assert=require('assert');
const path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../platform/primoscore_server/static/customer.js'),'utf8');
const fields=source.slice(source.indexOf('function questionFields()'),source.indexOf('function save('));
const result=source.slice(source.indexOf('function result()'),source.indexOf('\nasync function booking',source.indexOf('function result()')+1));
const ctx={step:4,answers:{purpose:'renovation'},questionField:k=>`FIELD:${k}`,input:()=>'',istatFields:()=>'',state:{client:{email:'test'},result:{strengths:[],warnings:[],metrics:{},consap:{explanation:'Fondo test'}}},esc:v=>v||'',root:{},accessNotice:()=>'',link:()=>'',money:()=>'',consultantName:()=>''};vm.createContext(ctx);vm.runInContext(fields,ctx);
for(const purpose of ['renovation','surrogation','second_home','consolidation']){ctx.answers={purpose};assert(!/isee|otherHome|Fondo/.test(ctx.questionFields()));}
ctx.answers={purpose:'first_home'};assert(/FIELD:iseeBand/.test(ctx.questionFields()));
ctx.answers={purpose:'renovation',iseeBand:'over_40k',otherHome:'yes',iseeUnknown:'yes'};ctx.$=()=>({querySelectorAll:()=>[]});ctx.collect();assert.deepEqual(Object.keys(ctx.answers),['purpose']);
vm.runInContext(result,ctx);
for(const purpose of ['renovation','surrogation','second_home','consolidation']){ctx.answers={purpose};ctx.result();assert(!/Fondo Prima Casa|Requisiti ufficiali Consap/.test(ctx.root.innerHTML));}
ctx.answers={purpose:'first_home'};ctx.result();assert(/Fondo Prima Casa/.test(ctx.root.innerHTML));
console.log('Questionario, cambio finalità e report verificati.');
