import importlib.util
import datetime as dt
import os
from pathlib import Path
import pytest
from botocore.exceptions import ClientError

spec=importlib.util.spec_from_file_location('app',Path(__file__).parents[1]/'backend/app.py')
app=importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k,v in {'TABLE_NAME':'test','IP_SALT':'test-only-salt','ORIGIN_SECRET':'test-origin','MODEL_ID':'test-model'}.items():
        monkeypatch.setenv(k,v)

def test_input_limits_and_unicode():
    assert app.validate({'prompt':'Eine konkrete Aufgabe'})[1]=='general'
    for body in ([],{'prompt':'kurz'},{'prompt':'😀'*1300},{'prompt':'x'*5001},{'prompt':'Ein guter Auftrag','agent':'admin'}):
        with pytest.raises(ValueError): app.validate(body)

def test_daily_ip_rotation_and_ipv6_grouping():
    assert app.ip_key('2001:db8::1','2026-01-01')==app.ip_key('2001:db8::2','2026-01-01')
    assert app.ip_key('192.0.2.1','2026-01-01')!=app.ip_key('192.0.2.1','2026-01-02')

def test_all_limits_are_one_conditional_transaction():
    class DB:
        def transact_write_items(self,**kwargs): self.kwargs=kwargs
    db=DB()
    app.reserve(db,'192.0.2.1',dt.datetime(2026,9,30,23,59,tzinfo=dt.timezone.utc))
    actions=db.kwargs['TransactItems']
    assert len(actions)==3
    assert [a['Update']['ExpressionAttributeValues'][':limit']['N'] for a in actions]==['200','15','5']
    assert actions[0]['Update']['Key']['pk']['S']=='month:2026-09'
    assert all('ConditionExpression' in a['Update'] for a in actions)

def test_rejects_direct_origin_without_aws_call(monkeypatch):
    monkeypatch.setattr(app.boto3,'client',lambda *a,**k:pytest.fail('Unexpected AWS call'))
    assert app.handler({'headers':{}},None)['statusCode']==403

def test_quota_failure_never_calls_model(monkeypatch):
    class DB:
        def transact_write_items(self,**kwargs):
            raise ClientError({'Error':{'Code':'TransactionCanceledException'}},'TransactWriteItems')
    def client(name,**kwargs):
        assert name=='dynamodb'
        return DB()
    monkeypatch.setattr(app.boto3,'client',client)
    event={'headers':{'x-promptwerk-origin':'test-origin','x-promptwerk-ip':'192.0.2.1','content-type':'application/json'},'rawPath':'/api/refine','requestContext':{'http':{'method':'POST'}},'body':'{"prompt":"Eine konkrete Aufgabe"}'}
    assert app.handler(event,None)['statusCode']==429

def test_model_schema_and_fences():
    assert app.parse_result('```json\n{"prompt":"Test","changes":[],"questions":[]}\n```')['prompt']=='Test'
    for text in ('nonsense','[]','{"prompt":"","changes":[],"questions":[]}','{"prompt":"yes","changes":"no","questions":[]}'):
        with pytest.raises((ValueError,TypeError)):app.parse_result(text)
