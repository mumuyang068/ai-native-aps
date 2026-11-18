import type {DataVersion,ImportTask} from '../api/contracts';

const TARGETS:Record<string,string[]>={
  orders:['订单'],
  production:['产品物料','BOM','工艺路线','生产配方','生产资源','工作日历'],
  inventory:['库存','单位换算'],
  purchasing:['供应商','供应来源','采购台账'],
  customers:['订单'],
};

export function reviewSections(task:ImportTask,groups:Record<string,string>){
  return Object.entries(groups).map(([id,label])=>{
    const sheets=Object.entries(task.source.sheets)
      .filter(([name])=>id==='mappings'||(TARGETS[id]??[]).includes(task.mappings[name]?.target_sheet??''))
      .map(([name,sheet])=>({name,rows:sheet.rows.length,target:task.mappings[name]?.target_sheet??null}));
    return {id,label,sheets,confirmed:!!task.confirmations[id],rawRows:sheets.reduce((n,s)=>n+s.rows,0)};
  });
}

export function currentDataVersion(task:ImportTask,versions:DataVersion[]){
  return versions.find(v=>v.import_id===task.id&&task.version_ids.includes(v.id)&&
    ('current_version_id' in task?v.id===task.current_version_id:v.import_revision===task.revision));
}
