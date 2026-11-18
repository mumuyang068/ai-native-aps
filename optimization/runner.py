"""Dedicated dependency environment, exact problem input, no enterprise stores."""
import argparse,json
from pathlib import Path

def main():
 parser=argparse.ArgumentParser();parser.add_argument('problem');parser.add_argument('output');parser.add_argument('--algorithm',choices=['cp_sat','vns','tabu','late_acceptance','pyjobshop'],required=True);parser.add_argument('--budget',type=int,required=True);args=parser.parse_args()
 source=Path(args.problem).resolve();target=Path(args.output).resolve()
 if source.parent!=target.parent or source.suffix!='.json' or target.suffix!='.json':raise ValueError('OPTIMIZER_RUNNER_PATH')
 from scripts.data_link_guard import install_sql_audit
 install_sql_audit(source.parent)
 problem=json.loads(source.read_text('utf8'))
 if args.algorithm=='cp_sat':
  from .cp_sat import solve
  result=solve(problem,budget_seconds=args.budget)
 elif args.algorithm=='pyjobshop':
  from .pyjobshop_adapter import solve_pyjobshop
  result=solve_pyjobshop(problem,budget_seconds=args.budget)
 else:
  from .search import search
  result=search(problem,budget_seconds=args.budget,strategy=args.algorithm)
 target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')

if __name__=='__main__':main()
