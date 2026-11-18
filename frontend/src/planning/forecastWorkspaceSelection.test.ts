import {it,expect} from 'vitest';
import {forecastWorkspaceVersion,forecastWorkspaceSearch} from './forecastWorkspaceSelection';
it('restores the exact selected version only inside its original scope and otherwise uses newest',()=>{
 const q=forecastWorkspaceSearch(new URLSearchParams('analysis=forecast-netting'),'scope','OLD','R1');
 expect(forecastWorkspaceVersion(q,'scope',[{id:'NEW'},{id:'OLD'}])).toBe('OLD');
 expect(forecastWorkspaceVersion(q,'other-scope',[{id:'NEW'},{id:'OLD'}])).toBe('NEW');
 expect(forecastWorkspaceVersion(q,'scope',[{id:'NEW'}])).toBe('NEW');
 expect(q.get('forecast_row')).toBe('R1');expect(q.get('analysis')).toBe('forecast-netting');
});
