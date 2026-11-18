"""Durable clarification state; pending requests are not orders or solve jobs."""
import json
import sqlite3
import uuid
from pathlib import Path

class SessionStore:
    def __init__(self,root):
        self.path=Path(root)/'interactions.sqlite3'
        with sqlite3.connect(self.path) as db:db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, history TEXT NOT NULL, pending TEXT)')

    def get(self,session_id):
        with sqlite3.connect(self.path) as db:row=db.execute('SELECT history,pending FROM sessions WHERE id=?',(session_id,)).fetchone()
        return {'history':json.loads(row[0]),'pending':json.loads(row[1]) if row[1] else None} if row else {'history':[],'pending':None}

    def get_existing(self,session_id):
        with sqlite3.connect(self.path) as db:row=db.execute('SELECT history,pending FROM sessions WHERE id=?',(session_id,)).fetchone()
        return {'history':json.loads(row[0]),'pending':json.loads(row[1]) if row[1] else None} if row else None

    def _update(self,session_id,history=None,pending='KEEP'):
        with sqlite3.connect(self.path,timeout=10) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT history,pending FROM sessions WHERE id=?',(session_id,)).fetchone()
            old_history=json.loads(row[0]) if row else []
            old_pending=json.loads(row[1]) if row and row[1] else None
            text=json.dumps(old_history+history if history is not None else old_history,ensure_ascii=False)
            value=old_pending if pending=='KEEP' else pending
            db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?,?)',(session_id,text,json.dumps(value,ensure_ascii=False) if value else None))

    def append_turn(self,session_id,messages):self._update(session_id,history=messages)

    def set_pending(self,session_id,intent,known_fields,missing_fields):
        old=self.get(session_id)['pending']
        pending={'id':old['id'] if old and old['intent']==intent else 'PENDING-'+uuid.uuid4().hex[:16],
                 'state':'NEEDS_CONFIRMATION','intent':intent,'known_fields':known_fields,'missing_fields':missing_fields,
                 'order_created':False,'solve_submitted':False}
        self._update(session_id,pending=pending)
        return pending

    def clear_pending(self,session_id):self._update(session_id,pending=None)
