# Frontend component attribution and migration boundary

This product uses the publicly distributed Siemens Industrial Experience **Classic** theme. It does not use Siemens' proprietary Corporate Brand Theme or Siemens trademarks and is not represented as a Siemens product.

| Package | Locked version | License | Upstream |
|---|---|---|---|
| @siemens/ix | 5.3.0 | MIT | https://github.com/siemens/ix |
| @siemens/ix-react | 5.3.0 | MIT | https://github.com/siemens/ix |
| @siemens/ix-icons | 3.7.0 | MIT | https://github.com/siemens/ix-icons |
| @siemens/ix-aggrid | 5.1.0 | MIT | https://github.com/siemens/ix |
| @siemens/ix-echarts | 4.1.1 | MIT | https://github.com/siemens/ix |
| ag-grid-community, ag-grid-react | 35.3.1 | MIT | https://github.com/ag-grid/ag-grid |
| echarts | 6.1.0 | Apache-2.0 | https://github.com/apache/echarts |

The original package LICENSE, NOTICE and READMEOSS files remain part of their npm distributions. They must be retained with any distribution requiring those notices. The APS-IX-01 delivery archive contains the original official package tarballs and notices, with SHA512 checked against registry integrity. No AG Grid Enterprise package or feature is used; Excel export, pivot and enterprise grouping are outside this adoption.

Apache ECharts: Copyright 2017–2026 The Apache Software Foundation. This product includes software developed at The Apache Software Foundation (https://www.apache.org/).

## Implementation ownership

- `main.tsx` loads the public iX stylesheet. `tokens.css` maps existing business roles to iX variables; `ix-product.css` adapts existing professional geometry.
- `Controls.tsx` delegates standard buttons/icons/tabs/selects/messages/spinners/dialogs to iX. Callers retain business permissions, confirmation, cancellation and stale-action guards.
- `WorkspaceUi.tsx` delegates status/workflow steps to iX. Remaining Ant Input/Checkbox consumers use a temporary bridge reading the same iX colour tokens; this is not a complete Ant migration.
- `ProfessionalGrid.tsx` is a read-only AG Grid Community adapter. Real consumers are manufacturing operations, confirmed purchase orders and receipt records. Stable scope and row identity remain with the caller.
- `PlanningChart.tsx` delegates rendering/lifecycle to ECharts with `theme-classic-light`. `ixChartModel.ts` preserves native event order, null values, units and plan/version identity. Retired SVG icon/old inventory plot rules must not size chart SVGs.
- The professional routing Gantt remains domain-owned. Display zoom/pan does not edit operation dates, resource calendars, protected orders or capacity. Native resource evidence supplies actual assignments; machine qualification is scoped to the selected operation.

The original P2, PlanningService, Worker, RunStore, DraftStore, business profiles and ten MCP contracts are retained. No component library decides inventory availability, QC release, machine qualification or execution permission. Historical calculations are not new calculations, and an uncalculated draft is not a baseline result.

Build with Node >=22.19.0 and the committed npm lockfile. Vitest 4.1.12 is a bounded development-dependency security patch from the former 4.1.11 lock. Existing professional SVGs, small tables, precise native datetime inputs and other unmigrated consumers are retained; this notice does not claim full-site migration or production readiness.
