/* Assessment display only; canonical records are never changed. */
(() => {
 'use strict';
 const data=window.ASSESSMENT, byId=id=>document.getElementById(id);
 const fields=['pass','variable','family','configuration'];
 function option(select,value,label){const o=document.createElement('option');o.value=value;o.textContent=label;select.append(o);}
 Object.keys(data.tables).forEach(name=>option(byId('table'),name,name.replaceAll('_',' ')));
 function fillFilters(){const rows=data.tables[byId('table').value];for(const field of fields){const s=byId(field);s.replaceChildren();option(s,'*','All');const vs=[...new Set(rows.map(r=>r[field]===null?'__null':r[field]).filter(v=>v!==undefined))].sort();vs.forEach(v=>option(s,v,v==='__null'?'Record level':v==='U'?'U exploratory':v));}byId('pass').value='primary';if(!byId('pass').value)byId('pass').value='*';render();}
 function filtered(){return data.tables[byId('table').value].filter(r=>fields.every(k=>byId(k).value==='*'||String(r[k]===null?'__null':r[k])===byId(k).value));}
 function display(v){if(v===null||v===undefined)return 'null';if(typeof v==='object')return JSON.stringify(v);return String(v);}
 const dialog=byId('record-dialog');
 function openRecord(row){
  byId('record-id').textContent=row.row_id;
  byId('record').textContent=JSON.stringify(row,null,2);
  dialog.showModal();
  document.body.classList.add('record-open');
 }
 byId('record-close').onclick=()=>dialog.close();
 dialog.addEventListener('close',()=>document.body.classList.remove('record-open'));
 dialog.addEventListener('click',event=>{
  if(event.target!==dialog)return;
  const box=dialog.getBoundingClientRect();
  if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)dialog.close();
 });
 function render(){
  const rs=filtered();
  const keys=['row_id','pass','family','variable','configuration','comparison','task','cohort','metric','paired','hit','evaluated_miss','unavailable_miss','absent_capability_miss','TP','FP','FN','TN','exposure','segment_starts','category','diagnostic_hit','comparable_opportunities','raw_positive','side'].filter(k=>rs.some(r=>k in r));
  byId('count').textContent=rs.length+' complete records in the current selection';
  byId('head').replaceChildren();
  const header=document.createElement('tr');
  for(const key of keys){const th=document.createElement('th');th.textContent=key;th.scope='col';header.append(th);}
  byId('head').append(header);
  byId('body').replaceChildren();
  for(const row of rs.slice(0,200)){
   const tr=document.createElement('tr');
   for(const key of keys){
    const td=document.createElement('td');
    if(key==='row_id'){
     const button=document.createElement('button');
     button.type='button';button.className='record-link';button.textContent=display(row[key]);
     button.setAttribute('aria-label','View complete record '+row.row_id);
     button.setAttribute('aria-haspopup','dialog');button.setAttribute('aria-controls','record-dialog');
     button.onclick=()=>openRecord(row);td.append(button);
    }else td.textContent=display(row[key]);
    tr.append(td);
   }
   byId('body').append(tr);
  }
  byId('limit').textContent=rs.length>200?'Showing the first 200 records; filtered exports include all '+rs.length+'.':'';
  document.dispatchEvent(new Event('assessment:rendered'));
 }
 function csv(rs){const keys=[...new Set(rs.flatMap(r=>Object.keys(r)))];const quote=v=>'"'+String(v).replaceAll('"','""')+'"';return [...[keys.concat('record_json').map(quote).join(',')],...rs.map(r=>keys.map(k=>quote(JSON.stringify(r[k]===undefined?null:r[k]))).concat(quote(JSON.stringify(r))).join(','))].join('\r\n');}
 function download(text,ext){const a=document.createElement('a'),url=URL.createObjectURL(new Blob([text],{type:ext==='json'?'application/json':'text/csv'}));a.href=url;a.download=byId('table').value+'-filtered.'+ext;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 byId('table').onchange=fillFilters;fields.forEach(k=>byId(k).onchange=render);byId('json').onclick=()=>download(JSON.stringify(filtered(),null,2),'json');byId('csv').onclick=()=>download(csv(filtered()),'csv');
 window.assessmentViewer={filtered,csv,render,fillFilters};fillFilters();
})();
