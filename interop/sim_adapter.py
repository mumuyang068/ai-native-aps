"""Real HTTP only: APS never imports a simulator business Owner."""
from p21.live_connector import LiveSourceClient,LiveSourceHTTPError


class SimAdapter:
    def __init__(self,configuration):
        self.configuration=configuration
        self.client=LiveSourceClient(configuration,configuration['operator_token'])
        self.reader=LiveSourceClient(configuration,configuration['read_token'])
        self.path='/v1/sim/'+configuration['branch_id']

    def _request(self,path,body=None,*,reader=False):
        try:return (self.reader if reader else self.client).request(path,body=body)
        except LiveSourceHTTPError as exc:
            if exc.status>=500 or exc.status in (301,302,307,308,408):raise ConnectionError('EXTERNAL_STATUS_UNKNOWN') from None
            raise
        except ValueError as exc:
            if str(exc)=='LIVE_SOURCE_UNAVAILABLE':raise ConnectionError('EXTERNAL_UNAVAILABLE') from None
            raise

    def send(self,command):
        try:
            result=self._request(self.path+'/publication',dict(payload=command,
                expected_revision=command['payload']['world_revision'],idempotency_key=command['command_id']))
        except LiveSourceHTTPError as exc:
            if exc.status not in (403,409,422):raise ConnectionError('EXTERNAL_STATUS_UNKNOWN') from None
            return dict(command_id=command['command_id'],request_hash=command['request_hash'],state='REJECTED',code=str(exc))
        receipt=result.get('receipt',result)
        return dict(receipt,state=receipt.get('state',receipt.get('status')))

    def lookup(self,command_id):
        try:result=self._request(self.path+'/publication/'+command_id,reader=True)
        except ValueError as exc:
            if str(exc) in ('PUBLICATION_NOT_FOUND','PUBLICATION_COMMAND_NOT_FOUND'):return None
            raise
        receipt=result.get('receipt',result)
        return dict(receipt,state=receipt.get('state',receipt.get('status')))
