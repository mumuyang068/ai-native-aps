import {it,expect} from 'vitest';
import {inventoryLedgerEvents,inventoryPoints} from './analysisModel';
import {inventoryChartData} from './ixChartModel';
it('excludes an end-boundary transaction from the ledger without removing graph continuity',()=>{
 const a='2026-11-01T00:00:00+08:00',b='2026-12-01T00:00:00+08:00';
 const events=[{id:'first',at:a,quantity:'0.125',balance:'1.125',native_sequence:2},{id:'qc',at:a,quantity:'0',balance:'1.125',native_sequence:3},{id:'next',at:b,quantity:'2',balance:'3.125'}];
 expect(inventoryLedgerEvents(events,a,b).map(e=>e.id)).toEqual(['first','qc']);
 expect(inventoryLedgerEvents(events,a,b)[0].quantity).toBe('0.125');
 expect(inventoryPoints({opening:'1',events,complete:true,verified_through:b},a,b).at(-1)?.at).toBe(Date.parse(b));
 const chart=inventoryChartData({opening:'1',events,complete:true,verified_through:b},Date.parse(a),Date.parse(b));
 expect(chart.map(p=>p.eventId).filter(Boolean)).toEqual(['first','qc']);
 expect(chart.at(-1)?.value).toEqual([Date.parse(b),1.125]);
});
