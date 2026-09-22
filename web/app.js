'use strict';
const $=id=>document.getElementById(id);
const input=$('prompt'), encoder=new TextEncoder();
let busy=false;
input.addEventListener('input',()=>{const n=encoder.encode(input.value).length;$('counter').textContent=`${n.toLocaleString('de-DE')} / 5.000 Bytes`;$('counter').classList.toggle('over',n>5000);input.setCustomValidity(n>5000?'Bitte auf höchstens 5.000 UTF-8-Bytes kürzen.':'');});
$('example').addEventListener('click',()=>{if(busy)return;input.value='Mein Coding-Agent soll eine langsame Suchfunktion in einer bestehenden Web-App verbessern. Die bisherigen Filter und Ergebnisse müssen gleich bleiben. Er soll zuerst die Ursache untersuchen, dann eine möglichst kleine Änderung umsetzen und zeigen, ob die Suche schneller geworden ist.';$('agent').value='coding';input.dispatchEvent(new Event('input'));input.focus();});
function list(id,values){$(id).replaceChildren(...values.map(value=>{const li=document.createElement('li');li.textContent=value;return li;}));}
function setStatus(message,error=false){$('status').textContent=message;$('status').classList.toggle('error',error);}
$('refine-form').addEventListener('submit',async event=>{
  event.preventDefault();if(busy||!event.target.reportValidity())return;
  busy=true;$('submit').disabled=true;$('example').disabled=true;$('copy').disabled=true;$('submit-label').textContent='Wird verfeinert …';$('empty').hidden=true;$('result').hidden=true;$('loading').hidden=false;setStatus('');
  const payload=JSON.stringify({prompt:input.value,agent:$('agent').value,style:$('style').value});
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),65000);
  try{
    const digest=await crypto.subtle.digest('SHA-256',encoder.encode(payload));
    const hash=Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,'0')).join('');
    const res=await fetch('/api/refine',{method:'POST',headers:{'Content-Type':'application/json','x-amz-content-sha256':hash},body:payload,signal:controller.signal});
    let data;try{data=await res.json();}catch{throw new Error(res.status===429?'Promptwerk pausiert gerade zum Schutz des Nutzungskontingents. Bitte später erneut versuchen.':'Promptwerk ist gerade nicht verfügbar. Bitte später erneut versuchen.');}
    if(!res.ok)throw new Error(data.error||'Bitte später erneut versuchen.');
    if(typeof data.prompt!=='string')throw new Error('Die Antwort konnte nicht gelesen werden.');
    $('refined').value=data.prompt;list('changes',data.changes||[]);list('questions',data.questions||[]);$('changes-block').hidden=!data.changes?.length;$('questions-block').hidden=!data.questions?.length;$('result').hidden=false;$('copy').disabled=false;setStatus('Bereit. Du kannst den Auftrag noch bearbeiten und dann kopieren.');
    if(matchMedia('(max-width:650px)').matches)$('output-title').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth',block:'start'});
  }catch(error){$('empty').hidden=false;setStatus(error.name==='AbortError'?'Die Antwort dauert zu lange. Bitte später erneut versuchen. Dieser Versuch kann zum Kontingent zählen.':error.message,true);}
  finally{clearTimeout(timeout);busy=false;$('loading').hidden=true;$('submit').disabled=false;$('example').disabled=false;$('submit-label').textContent='Prompt verfeinern';}
});
$('copy').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('refined').value);setStatus('Auftrag kopiert. Bereit für deinen Agenten.');}catch{$('refined').focus();$('refined').select();setStatus('Bitte den markierten Auftrag manuell kopieren.');}});
const content={limits:['Fair geteilt. Klar begrenzt.',`<p>Promptwerk ist eine kleine, kostenlose Werkstatt ohne Anmeldung. Damit sie bezahlbar bleibt, gibt es ein gemeinsames Kontingent:</p><ul><li>5 Versuche pro IP-Adresse und Tag</li><li>15 Versuche pro Tag für alle zusammen</li><li>200 Versuche pro Kalendermonat für alle zusammen</li></ul><p>Die Zähler wechseln um 00:00 Uhr UTC. Gemeinsam genutzte Netzwerke teilen sich ein IP-Kontingent. Auch fehlgeschlagene Modellaufrufe zählen. Bei einem Budget- oder Sicherheitsalarm wird die Verarbeitung pausiert.</p><p>Pro Auftrag sind maximal 5.000 UTF-8-Bytes erlaubt. Umlaute und Emojis benötigen mehrere Bytes.</p>`],privacy:['Was mit deiner Eingabe passiert',`<p>Dein Auftrag wird zur Verfeinerung an Amazon Bedrock in Frankfurt gesendet. Promptwerk speichert weder deine Eingabe noch das Ergebnis dauerhaft und schreibt beides nicht in Anwendungslogs.</p><p>Für die Nutzungslimits speichern wir einen täglich wechselnden, mit einem geheimen Schlüssel abgeleiteten Hash deiner IP-Adresse. IPv6-Adressen werden dafür nach /64-Netz gruppiert. Die Zählereinträge werden nach spätestens 62 Tagen zur automatischen Löschung markiert; die Löschung kann danach verzögert erfolgen.</p><p>Für die Auslieferung verwenden wir Amazon CloudFront. Dabei verarbeitet AWS technisch notwendige Verbindungsdaten. Promptwerk verwendet keine Analyse-Tracker, Marketing-Cookies oder externe Schriftanbieter.</p><p>Bitte gib keine Zugangsdaten oder vertraulichen Informationen ein. Eingabe und Ergebnis bleiben nur während der geöffneten Sitzung in dieser Seite.</p>`]};
for(const type of ['limits','privacy'])$(type+'-button').addEventListener('click',()=>{$('dialog-title').textContent=content[type][0];$('dialog-content').innerHTML=content[type][1];$('info-dialog').showModal();});
$('close-dialog').addEventListener('click',()=>$('info-dialog').close());
