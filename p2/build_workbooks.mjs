import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Workbook, SpreadsheetFile} from '@oai/artifact-tool';
const here=path.dirname(fileURLToPath(import.meta.url));
const spec=JSON.parse(await fs.readFile(path.join(here,'artifacts/authoring-spec.json'),'utf8'));
await fs.mkdir(path.join(here,'samples'),{recursive:true});
await fs.mkdir(path.join(here,'artifacts/previews'),{recursive:true});
function col(n){let x='';for(;n;n=Math.floor((n-1)/26))x=String.fromCharCode(65+(n-1)%26)+x;return x;}
const manifest=[];
for(const [ci,c] of spec.cases.entries()){
  const wb=Workbook.create();
  const guide={name:'使用说明',headers:['项目','内容'],rows:[
    ['样本',c.name],['用途',c.description],['工厂','小型服装厂；单款黑色 T 恤，裁剪→缝制→包装'],
    ['业务来源','冻结 P0 模拟数据；无真实企业、订单、设备或供应商数据'],
    ['读取方式','业务表第 5 行表头，第 6 行开始为明细；先核对，再形成导入版本'],
    ['时间口径','时间文本示例：2026-10-14 09:00:00〔+08:00〕；保留明确时区与具体时刻'],
    ['产能口径','沿用 P0 已声明的模拟工时；不得借本样本为真实工厂补产能'],
    ['来源版本','p2.0-sim-v1；每行记录来源，导出报告含文件哈希与单元格位置'],
    ['核对责任','生产经理：工艺/产能/日历；仓库：库存/预留；采购：提前期/在途'],
    ['计算范围','本轮只验证确定性映射及原生输入；不执行新的规划计算'],
    ['原始文件','保存只读原件；人工修正生成新版本，不覆盖来源'],
  ],note:'先用已有资料定位缺口，再补齐会阻止排产的关键事实。'};
  const all=[guide,...c.sheets];
  for(const [si,t] of all.entries()){
    const sh=wb.worksheets.add(t.name);sh.showGridLines=false;sh.tabColor=si===0?'#17365D':'#507399';
    const last=col(t.headers.length),end=5+t.rows.length;
    sh.getRange(`A1:${last}${end}`).format.font={name:'Arial',size:10,color:'#243746'};
    sh.getRange(`A1:${last}${end}`).format.rowHeight=42;
    sh.getRange(`A1:${last}${end}`).format.columnWidth=21;
    sh.getRange(`A1:${last}${end}`).format.wrapText=true;
    sh.getRange(`A1:${last}${end}`).format.verticalAlignment='center';
    sh.getRange('A1').values=[[`${t.name} · ${c.name.slice(3)}`]];
    const titleLast=col(Math.min(t.headers.length,7));
    sh.getRange(`A1:${titleLast}1`).merge();sh.getRange('A1').format.font={name:'Arial',size:15,bold:true,color:'#17365D'};
    sh.getRange('A1').format.rowHeight=34;
    sh.getRange('A2').values=[[t.note]];sh.getRange(`A2:${titleLast}3`).merge();
    sh.getRange(`A2:${titleLast}3`).format.fill='#F2F5F8';
    sh.getRange(`A5:${last}5`).values=[t.headers];
    // Explicit timezone text prevents spreadsheet/render auto date coercion and UTC shifts.
    sh.getRange(`A6:${last}${end}`).values=t.rows.map(row=>row.map(v=>typeof v==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$/.test(v)?v.slice(0,19).replace('T',' ')+'〔'+v.slice(19)+'〕':v));
    sh.getRange(`A5:${last}5`).format.fill='#17365D';
    sh.getRange(`A5:${last}5`).format.font={name:'Arial',size:10,bold:true,color:'#FFFFFF'};
    sh.getRange(`A5:${last}5`).format.rowHeight=36;
    sh.freezePanes.freezeRows(5);sh.freezePanes.freezeColumns(1);
    // Explicit source and time columns are wide enough for their retained text.
    for(const [j,h] of t.headers.entries()){
      const letter=col(j+1),rng=sh.getRange(`${letter}6:${letter}${end}`);
      if(/时间|工作开始|工作结束|交期|时点|到货/.test(h)){
        sh.getRange(`${letter}:${letter}`).format.columnWidth=33;
        rng.setNumberFormat('@');
      }
      if(h==='来源记录')sh.getRange(`${letter}:${letter}`).format.columnWidth=53;
      if(h==='来源说明')sh.getRange(`${letter}:${letter}`).format.columnWidth=60;
      if(h==='字段路径')sh.getRange(`${letter}:${letter}`).format.columnWidth=43;
      if(h==='确认值')sh.getRange(`${letter}:${letter}`).format.columnWidth=48;
      if(t.rows.some(r=>typeof r[j]==='number'))rng.setNumberFormat(t.rows.every(r=>typeof r[j]!=='number'||Number.isInteger(r[j]))?'#,##0':'#,##0.00');
    }
    if(si===0){sh.getRange('A:A').format.columnWidth=21;sh.getRange('B:B').format.columnWidth=90;sh.getRange(`A6:B${end}`).format.rowHeight=39;}
    for(const [ri,r] of t.rows.entries())for(const [j,v] of r.entries())if(v===null&&t.name!=='库存')sh.getRange(`${col(j+1)}${ri+6}`).format.fill='#FFF0C2';
    if(si>0)sh.tables.add(`A5:${last}${end}`,true,`Case${ci+1}Sheet${si}`);
  }
  wb.recalculate();
  const check=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:100}});
  const detail=await wb.inspect({kind:'table',range:'订单!A5:H6',include:'values',tableMaxRows:3,tableMaxCols:8,maxChars:2000});
  await fs.writeFile(path.join(here,`artifacts/${c.name}-inspection.json`),JSON.stringify({check,detail},null,2));
  const output=await SpreadsheetFile.exportXlsx(wb);await output.save(path.join(here,'samples',`${c.name}.xlsx`));
  // Every sheet type plus each case's guide is rendered for visual inspection.
  for(const t of (ci===0?all:[guide,...c.sheets.filter(s=>c.changes.some(x=>x.sheet===s.name)||['订单','工艺路线'].includes(s.name))])){
    const maxcol=Math.min(t.headers.length,7),rows=Math.min(5+t.rows.length,12);
    const preview=await wb.render({sheetName:t.name,range:`A1:${col(maxcol)}${rows}`,scale:1,format:'png'});
    await fs.writeFile(path.join(here,'artifacts/previews',`${c.name}-${t.name}.png`),new Uint8Array(await preview.arrayBuffer()));
    if(t.headers.length>7){
      const right=await wb.render({sheetName:t.name,range:`H5:${col(t.headers.length)}${rows}`,scale:1,format:'png'});
      await fs.writeFile(path.join(here,'artifacts/previews',`${c.name}-${t.name}-右侧.png`),new Uint8Array(await right.arrayBuffer()));
    }
  }
  manifest.push({file:`${c.name}.xlsx`,description:c.description,changes:c.changes});
  console.log(`Created ${c.name}.xlsx (${all.length} sheets)`);
}
await fs.writeFile(path.join(here,'contracts/samples.json'),JSON.stringify({source_sha256:spec.source_sha256,cases:manifest},null,2)+'\n');
