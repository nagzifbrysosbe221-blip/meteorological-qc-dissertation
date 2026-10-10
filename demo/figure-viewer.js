/* Select original published figures and their matching, unchanged data files. */
(() => {
  'use strict';
  const el=id=>document.getElementById(id);
  const catalog=JSON.parse(el('figure-catalog').textContent);
  const group=el('figure-group'),pass=el('figure-pass'),picture=el('selected-figure');
  let index=0,requestId=0,controller;
  const selection=()=>catalog.figures.filter(f=>f.group===group.value&&f.pass===pass.value);
  async function render(){
    const figures=selection(),figure=figures[index],id=++requestId;
    if(controller)controller.abort();
    controller=new AbortController();
    const groupInfo=catalog.groups.find(g=>g.id===group.value);
    const passInfo=catalog.passes.find(p=>p.id===pass.value);
    const title=groupInfo.label+' · '+passInfo.label;
    el('figure-title').textContent=title;
    el('figure-description').textContent=groupInfo.description;
    el('figure-detail').textContent=figure.detail;
    el('figure-detail').hidden=!figure.detail;
    el('figure-pager').hidden=figures.length===1;
    el('figure-position').textContent=`Figure ${index+1} of ${figures.length}`;
    // Keep both controls focusable while paging, including at the first/last figure.
    el('figure-previous').setAttribute('aria-disabled',String(index===0));
    el('figure-next').setAttribute('aria-disabled',String(index===figures.length-1));
    el('figure-filename').textContent=figure.file;
    el('figure-original').href='figures/'+figure.file;
    const dataUrl='figures/'+figure.data;
    el('figure-json-open').href=dataUrl;
    el('figure-json-download').href=dataUrl;
    picture.hidden=true;
    el('figure-image-status').textContent='Loading figure…';
    picture.onload=()=>{if(id!==requestId)return;picture.hidden=false;el('figure-image-status').textContent='';};
    picture.onerror=()=>{if(id!==requestId)return;el('figure-image-status').textContent='The figure could not be displayed. Use the original figure link below.';};
    picture.alt=title+(figure.detail?' — '+figure.detail:'')+', with original labelled scope';
    picture.src='figures/'+figure.file;
    el('figure-data').textContent='';
    el('figure-data').hidden=true;
    el('figure-data-status').textContent='Loading plotted data…';
    el('plotted-data').setAttribute('aria-busy','true');
    try{
      const response=await fetch(dataUrl,{signal:controller.signal});
      if(!response.ok)throw new Error('Data request failed');
      const raw=await response.text();
      JSON.parse(raw); // Check it is JSON; display the original file text, without rewriting values.
      if(id!==requestId)return;
      el('figure-data').textContent=raw;
      el('figure-data').hidden=false;
      el('figure-data').scrollTop=0;
      el('figure-data-status').textContent='Complete plotted data: '+figure.data;
    }catch(error){
      if(id!==requestId)return;
      el('figure-data-status').textContent='The plotted data could not be loaded in this page. Use the JSON link above to open the saved file.';
    }finally{
      if(id===requestId)el('plotted-data').setAttribute('aria-busy','false');
    }
  }
  function changeSelection(){index=0;render();}
  group.addEventListener('change',changeSelection);
  pass.addEventListener('change',changeSelection);
  el('figure-previous').addEventListener('click',()=>{if(index>0){index--;render();}});
  el('figure-next').addEventListener('click',()=>{if(index<selection().length-1){index++;render();}});
  el('figure-controls').hidden=false;
  render();
})();
