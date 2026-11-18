"""Actual native ORM/date inverse, calendar, protection and atomic command tests."""
import copy
from datetime import timedelta
from odoo import Command
from odoo.tests import tagged
from .test_publication import TestPublication


@tagged('post_install','-at_install')
class TestProfessionalSchedule(TestPublication):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.calendar=cls.env['resource.calendar'].create({'name':'Explicit test calendar','tz':'UTC','company_id':cls.company.id,
            'attendance_ids':[Command.clear()]+[Command.create({'name':'work','dayofweek':str(d),'hour_from':8,'hour_to':17,'day_period':'morning'}) for d in range(5)]})
        cls.centers=cls.env['mrp.workcenter']
        cls.route=cls.env['mrp.routing.workcenter']
        cls.bom.allow_operation_dependencies=True
        previous=False
        for index,name in enumerate(['CUT','SEW','QC','PACK'],1):
            wc=cls.env['mrp.workcenter'].create({'name':name,'company_id':cls.company.id,'resource_calendar_id':cls.calendar.id,'default_capacity':1,'time_efficiency':100})
            op=cls.env['mrp.routing.workcenter'].create({'name':name,'sequence':index*10,'bom_id':cls.bom.id,'workcenter_id':wc.id,'time_mode':'manual','time_cycle_manual':1,
                'blocked_by_operation_ids':[Command.set(previous.ids)] if previous else []})
            cls.centers|=wc;cls.route|=op;previous=op

    def planned(self):
        create=self.command('CREATE-professional')
        receipt=self.service.publish(create);mo=self.env['mrp.production'].browse(receipt['external_document']['id'])
        mo.action_confirm();mo.button_plan()
        snapshot=self.service.inspect_manufacturing(mo.id)
        payload=copy.deepcopy(create['payload'])
        payload.update(external_document_id=mo.id,expected_write_date=snapshot['version']['production'],
            expected_workorders=snapshot['version']['workorders'],expected_master_sha256=snapshot['version']['masters_sha256'],
            expected_publication_command_id=create['command_id'],
            start_at=mo.date_start.isoformat()+'+00:00',end_at=mo.date_finished.isoformat()+'+00:00',constraints=[],
            operations=[dict(aps_operation_id=w.name,operation_id=w.operation_id.id,workcenter_id=w.workcenter_id.id,quantity='20',
                start_at=w.date_start.isoformat()+'+00:00',end_at=w.date_finished.isoformat()+'+00:00',
                predecessor_operation_ids=w.blocked_by_workorder_ids.operation_id.ids) for w in mo.workorder_ids])
        return mo,self.command('SCHEDULE-professional',action_type='SCHEDULE_MO',payload=payload)

    def test_another_same_product_root_cannot_be_rebound(self):
        mo,request=self.planned()
        request['suggestion_id']='OTHER-ROOT-SAME-PRODUCT'
        before=(mo.date_start,mo.date_finished)
        receipt=self.service.publish(request)
        self.assertEqual(receipt['state'],'REJECTED')
        self.assertEqual(receipt['code'],'EXTERNAL_PUBLICATION_LINEAGE_MISMATCH')
        self.assertEqual((mo.date_start,mo.date_finished),before)

    def test_real_alternative_workcenter_uses_native_resource_and_duration(self):
        mo,request=self.planned()
        original=self.centers[1]
        alternative=self.env['mrp.workcenter'].create({'name':'SEW real alternative','company_id':self.company.id,
            'resource_calendar_id':self.calendar.id,'default_capacity':1,'time_efficiency':100})
        original.alternative_workcenter_ids=[Command.link(alternative.id)]
        request['payload']['operations'][1]['workcenter_id']=alternative.id
        request['payload']['expected_master_sha256']=self.service.inspect_manufacturing(mo.id)['version']['masters_sha256']
        receipt=self.service.publish(request)
        self.assertEqual(receipt['state'],'ACCEPTED')
        self.assertEqual(mo.workorder_ids[1].workcenter_id,alternative)
        self.assertTrue(all(c['calendar_valid'] and c['capacity_valid'] and c['duration_valid'] and c['dependency_valid']
            for c in receipt['professional_snapshot']['schedule_checks']))

    def test_native_schedule_preserves_one_root_and_native_workorder_relationships(self):
        mo,request=self.planned();receipt=self.service.publish(request)
        self.assertEqual(receipt['state'],'ACCEPTED')
        self.assertEqual(len(receipt['professional_snapshot']['workorders']),4)
        self.assertTrue(all(r['calendar_valid'] and r['duration_valid'] and r['dependency_valid'] and r['capacity_valid'] for r in receipt['professional_snapshot']['schedule_checks']))
        self.assertEqual(self.service.publish(request),receipt)
        self.assertEqual(mo.qty_produced,0)

    def test_stale_child_version_rejects_atomically(self):
        mo,request=self.planned();before=[(w.id,w.date_start,w.date_finished) for w in mo.workorder_ids]
        key=next(iter(request['payload']['expected_workorders']));request['payload']['expected_workorders'][key]='2000-01-01T00:00:00.000000'
        receipt=self.service.publish(request)
        self.assertEqual(receipt['state'],'REJECTED');self.assertEqual(receipt['code'],'EXTERNAL_WORKORDER_VERSION_STALE')
        self.assertEqual(before,[(w.id,w.date_start,w.date_finished) for w in mo.workorder_ids])

    def test_illegal_alternative_and_dependency_do_not_change_original_plan(self):
        mo,request=self.planned();before=mo.date_finished
        request['payload']['operations'][0]['workcenter_id']=self.centers[-1].id
        receipt=self.service.publish(request)
        self.assertEqual(receipt['state'],'REJECTED');self.assertEqual(receipt['code'],'EXTERNAL_WORKCENTER_NOT_ALLOWED')
        self.assertEqual(mo.date_finished,before)

    def test_native_replanning_is_observable_without_editing_the_receipt(self):
        mo,request=self.planned();receipt=self.service.publish(request)
        self.assertEqual(receipt['state'],'ACCEPTED')
        mo.button_unplan();mo.date_start+=timedelta(days=1);mo.button_plan()
        current=self.service.inspect_manufacturing(mo.id)
        self.assertNotEqual(current['production']['date_start'],receipt['professional_snapshot']['production']['date_start'])
        self.assertEqual(self.service.get_receipt(request['command_id'])['professional_snapshot'],receipt['professional_snapshot'])
