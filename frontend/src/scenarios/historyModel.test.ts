import {it,expect} from 'vitest';
import {completedTurn} from './historyModel';
const messages=[{id:'s:0',role:'user',text:'查询'},{id:'s:2',role:'assistant',text:'旧结果'},{id:'s:3',role:'user',text:'查询'},{id:'s:5',role:'assistant',text:'新结果'}] as const;
it('reconciles a completed chat only after the pre-send history bookmark',()=>{expect(completedTurn([...messages],'s:2','查询')).toBe(true);expect(completedTurn([...messages].slice(0,3),'s:2','查询')).toBe(false);expect(completedTurn([...messages],'s:5','查询')).toBe(false);expect(completedTurn([...messages],'missing','查询')).toBe(false)});
