'use strict';
(() => {
  const examples = {
    ordinary: {station:'260',date:'20210615',hour:12,temperature:'123',humidity:'58'},
    cold: {station:'260',date:'20210115',hour:6,temperature:'-25',humidity:'86'},
    zero: {station:'260',date:'20210210',hour:8,temperature:'0',humidity:'58'},
    missing: {station:'260',date:'20210615',hour:12,temperature:'',humidity:'58'},
    midnight: {station:'260',date:'20211231',hour:24,temperature:'123',humidity:'58'},
    reference: {station:'240',date:'20210615',hour:12,temperature:'123',humidity:'58'}
  };
  const picker = document.getElementById('example');
  const buttons = [...document.querySelectorAll('[data-field]')];
  let field = 'temperature';
  const dateLabel = date => new Intl.DateTimeFormat('en-GB', {day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}).format(date);
  const getSourceDate = row => new Date(Date.UTC(Number(row.date.slice(0,4)),Number(row.date.slice(4,6))-1,Number(row.date.slice(6,8))));
  function render() {
    const row = examples[picker.value];
    const station = row.station === '260' ? 'De Bilt' : 'Schiphol';
    const sourceDate = getSourceDate(row);
    const timestamp = new Date(sourceDate.getTime() + row.hour*3600000);
    const temperature = row.temperature === '' ? 'missing' : `${Number(row.temperature)/10} °C`;
    Object.keys(row).forEach(key => {document.getElementById(`raw-${key}`).textContent = row[key] === '' ? 'blank' : row[key];});
    document.getElementById('decoded').textContent = `${station} · ${dateLabel(timestamp)} at ${String(timestamp.getUTCHours()).padStart(2,'0')}:00 UTC · Air temperature ${temperature} · Relative humidity ${row.humidity}%.`;
    document.getElementById('record-note').textContent = row.hour===24
      ? `The displayed timestamp is in ${timestamp.getUTCFullYear()}, but the original source date is ${dateLabel(sourceDate)}. This record stays in the ${sourceDate.getUTCFullYear()} source period.`
      : row.temperature === '' ? 'The temperature is missing, not zero. This row still contains a humidity measurement.' : `The original source date remains ${dateLabel(sourceDate)}.`;
    const descriptions = {
      station: ['STN / Station identifier',`${row.station} means ${station}`,row.station === '260' ? 'De Bilt is the target station: the location whose readings the system monitors. A station identifier does not identify one unchanged physical instrument.' : 'Schiphol supplies candidate reference information. It is a comparison source, not guaranteed correct ground truth. Its suitability is checked later.'],
      date: ['YYYYMMDD / Original source date',dateLabel(sourceDate),'Read the first four digits as the year, the next two as the month, and the last two as the day. The original source date determines the study period, before any hour-24 conversion.'],
      hour: ['HH / Source hour',row.hour === 24 ? 'Hour 24 becomes next-day midnight' : `Hour ${row.hour} becomes ${String(row.hour).padStart(2,'0')}:00 UTC`,row.hour===24 ? `Here, 31 December 2021 at hour 24 is displayed as 1 January 2022 at 00:00 UTC. It still belongs to the 2021 source period.` : 'The source uses hours 1 to 24. Times are interpreted in UTC. Hour 24 means 00:00 at the beginning of the following date, while the original date is preserved.'],
      temperature: ['T / Air temperature',row.temperature === '' ? 'Blank means missing' : `${row.temperature} becomes ${temperature}`,row.temperature === '' ? 'No temperature value is supplied in this cell. Keep it missing; do not replace it with zero or a guessed reading. The hourly record itself is still present.' : row.temperature === '0' ? 'Zero is a valid numerical value. Here it means 0 °C. It must remain distinguishable from a blank cell, which means the temperature is missing.' : 'KNMI stores T in tenths of a degree Celsius. Divide the source value by 10. This changes the unit representation, not the underlying measurement.'],
      humidity: ['U / Relative humidity',`${row.humidity} remains ${row.humidity}%`,'Humidity is already expressed as a percentage, so it is not divided by 10. The source field is U. The similarly named RH field means precipitation and is not used as humidity.']
    };
    const [label,title,description] = descriptions[field];
    const panel = document.getElementById('field-explanation');
    panel.querySelector('.eyebrow').textContent = label;
    panel.querySelector('h3').textContent = title;
    panel.querySelector('p').textContent = description;
    buttons.forEach(button => button.setAttribute('aria-pressed',String(button.dataset.field===field)));
  }
  picker.addEventListener('change',() => {field = picker.value === 'midnight' ? 'hour' : picker.value === 'reference' ? 'station' : 'temperature';render();});
  buttons.forEach(button=>button.addEventListener('click',()=>{field=button.dataset.field;render();}));
  render();
})();
