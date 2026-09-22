"""Explicit integration check. Briefly stops and then restores this app only."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import time
import urllib.request
import urllib.error
import boto3
from botocore.exceptions import ClientError

ROOT=Path(__file__).resolve().parents[1]
out=json.loads((ROOT/'.deployment/outputs.json').read_text())
session=boto3.Session(region_name='eu-central-1')
ddb=session.client('dynamodb')
lam=session.client('lambda')
budgets=session.client('budgets',region_name='us-east-1')
account=session.client('sts').get_caller_identity()['Account']
budget=budgets.describe_budget(AccountId=account,BudgetName='promptwerk-monthly')['Budget']
assert float(budget['BudgetLimit']['Amount'])==6
notifications=budgets.describe_notifications_for_budget(AccountId=account,BudgetName='promptwerk-monthly')['Notifications']
assert sorted(float(n['Threshold']) for n in notifications)==[3,5,6]
for n in notifications:
    subs=budgets.describe_subscribers_for_notification(AccountId=account,BudgetName='promptwerk-monthly',Notification=n)['Subscribers']
    assert any(s['SubscriptionType']=='EMAIL' for s in subs)
    if float(n['Threshold'])==6:assert any(s['SubscriptionType']=='SNS' and s['Address']==out['StopTopicArn'] for s in subs)
print('PASS: budget thresholds, email subscribers and automatic stop topic',flush=True)
tags=session.client('ce',region_name='us-east-1').list_cost_allocation_tags(TagKeys=['Project'])['CostAllocationTags']
assert tags[0]['Status']=='Active'
print('PASS: cost allocation tag active',flush=True)
try:
    urllib.request.urlopen(out['FunctionUrl'],timeout=10)
    raise AssertionError('Origin was publicly accessible')
except urllib.error.HTTPError as e:
    assert e.code==403
print('PASS: direct Lambda origin rejects unauthenticated access',flush=True)

# Reserved namespace, far-future date. Never modify real usage counters.
spec=importlib.util.spec_from_file_location('app',ROOT/'backend/app.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
os.environ['TABLE_NAME']=out['Table'];os.environ['IP_SALT']='integration-only'
date=dt.datetime(2099,1,1,tzinfo=dt.timezone.utc)
ips=['192.0.2.17','192.0.2.18']
keys=['month:2099-01','day:2099-01-01']+[f'ip:2099-01-01:{app.ip_key(ip,"2099-01-01")}' for ip in ips]
assert all('Item' not in ddb.get_item(TableName=out['Table'],Key={'pk':{'S':key}},ConsistentRead=True) for key in keys)
try:
    for _ in range(5):
        app.reserve(ddb,ips[0],date);time.sleep(1)
    try:app.reserve(ddb,ips[0],date);raise AssertionError('IP cap bypassed')
    except ClientError as e:assert e.response['Error']['Code']=='TransactionCanceledException'
    item=ddb.get_item(TableName=out['Table'],Key={'pk':{'S':keys[0]}},ConsistentRead=True)['Item']
    assert item['used']['N']=='5','Rejected IP request must not consume global slots'
    ddb.update_item(TableName=out['Table'],Key={'pk':{'S':keys[0]}},UpdateExpression='SET used = :n',ExpressionAttributeValues={':n':{'N':'200'}})
    try:app.reserve(ddb,ips[1],date);raise AssertionError('Month cap bypassed')
    except ClientError as e:assert e.response['Error']['Code']=='TransactionCanceledException'
    assert 'Item' not in ddb.get_item(TableName=out['Table'],Key={'pk':{'S':keys[-1]}},ConsistentRead=True)
    print('PASS: real DynamoDB IP/month caps and transaction rollback',flush=True)
finally:
    for key in keys:ddb.delete_item(TableName=out['Table'],Key={'pk':{'S':key}});time.sleep(.5)

previous=lam.get_function_concurrency(FunctionName=out['FunctionName'])['ReservedConcurrentExecutions']
assert previous==2,'App already stopped: do not override an existing alarm'
session.client('sns').publish(TopicArn=out['StopTopicArn'],Message='Promptwerk deployment verification: exercise automatic stop.')
stopped=False
for _ in range(20):
    if lam.get_function_concurrency(FunctionName=out['FunctionName']).get('ReservedConcurrentExecutions')==0:
        stopped=True;break
    time.sleep(2)
assert stopped,'SNS shutdown failed; investigate before opening app'
print('PASS: SNS alarm path set backend concurrency to zero',flush=True)
lam.put_function_concurrency(FunctionName=out['FunctionName'],ReservedConcurrentExecutions=previous)
assert lam.get_function_concurrency(FunctionName=out['FunctionName'])['ReservedConcurrentExecutions']==2
print('PASS: backend restored after controlled test',flush=True)
