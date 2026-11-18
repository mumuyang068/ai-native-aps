# Third-party software

AI Native APS connects to frePPLe Community as an external planning engine. Its
reference source is [frePPLe Community 9.19, fixed revision](https://github.com/frePPLe/frepple/tree/ab8430ca26f41bac1f54a8a30070642e91d9b3e9).
That revision publishes its [MIT terms in COPYING](https://github.com/frePPLe/frepple/blob/ab8430ca26f41bac1f54a8a30070642e91d9b3e9/COPYING).
The Git repository contains the APS adapter and APS planning task extensions.
Install the frePPLe runtime separately.

The UI uses React, Siemens Industrial Experience, AG Grid Community and
Apache ECharts. See [frontend notices](frontend/THIRD_PARTY_NOTICES.md) for
selected versions and attribution. Package managers obtain the dependencies
from their publishers. Preserve the original notices when redistributing them.

Optimization uses [Google OR-Tools](https://github.com/google/or-tools) as a
separate dependency. Odoo integration is intended for an independently
provisioned Odoo instance. Consult each upstream project for its license terms.

The separate [APS Odoo addon](integrations/odoo/aps_interop/) retains
the LGPL-3.0 license declared in its manifest. Its license texts are
[COPYING.LESSER](integrations/odoo/aps_interop/COPYING.LESSER) and
[COPYING](integrations/odoo/aps_interop/COPYING).
