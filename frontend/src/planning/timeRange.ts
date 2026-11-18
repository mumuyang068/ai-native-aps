function parts(iso:string|number,timezone:string){return Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(iso)).map(p=>[p.type,p.value]))}
export function siteDate(iso:string|number,timezone:string):string{const p=parts(iso,timezone);return `${p.year}-${p.month}-${p.day}`}
export function formatSiteTime(iso:string|null|undefined,timezone:string):string{if(!iso)return '未排定';const p=parts(iso,timezone);return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`}
export function addDays(date:string,count:number):string{const d=new Date(date+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+count);return d.toISOString().slice(0,10)}
export function localHourAt(date:string,hour:number,timezone:string):number{const target=Date.parse(date+'T'+String(hour).padStart(2,'0')+':00:00Z');let guess=target;for(let i=0;i<4;i++){const p=parts(guess,timezone),represented=Date.UTC(+p.year,+p.month-1,+p.day,+p.hour,+p.minute,+p.second),delta=target-represented;guess+=delta;if(!delta)break}return guess}
export function midnightAt(date:string,timezone:string):number{return localHourAt(date,0,timezone)}
export type DayWindow={date:string;start:number;end:number};
export function visibleDays(startDate:string,count:number,timezone:string):DayWindow[]{return Array.from({length:count},(_,i)=>{const date=addDays(startDate,i);return {date,start:midnightAt(date,timezone),end:midnightAt(addDays(date,1),timezone)}})}
