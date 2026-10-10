/* Filter the displayed bibliography; source citations and links are unchanged. */
(() => {
  'use strict';
  const query=document.getElementById('reference-query');
  const entries=Array.from(document.querySelectorAll('.reference-entry'));
  const fold=s=>s.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  const searchable=entries.map(entry=>fold(entry.querySelector('.reference-citation').textContent));
  function filter(){
    const terms=fold(query.value.trim()).split(/\s+/).filter(Boolean);
    let shown=0;
    entries.forEach((entry,index)=>{entry.hidden=!terms.every(term=>searchable[index].includes(term));if(!entry.hidden)shown++;});
    document.getElementById('reference-count').textContent=terms.length?`Showing ${shown} of ${entries.length} references.`:`Showing all ${entries.length} references.`;
    document.getElementById('no-results').hidden=shown!==0;
  }
  query.addEventListener('input',filter);
  document.getElementById('clear-search').addEventListener('click',()=>{query.value='';filter();query.focus();});
  document.getElementById('reference-search').hidden=false;
})();
