"""Deploy Promptwerk using the current AWS session. Domain purchase is never performed."""
import argparse
import importlib.util
import json
import mimetypes
import os
from pathlib import Path
import secrets
import time
import boto3
from botocore.exceptions import ClientError
from template import template

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.deployment'
STATE.mkdir(mode=0o700,exist_ok=True)
REGION='eu-central-1'
STACK='promptwerk'
TAGS=[{'Key':'Project','Value':'promptwerk'}]
def client(service,region=REGION):return boto3.client(service,region_name=region)
def save(name,data):
    path=STATE/name
    path.write_text(json.dumps(data,indent=2,default=str)+'\n')
    path.chmod(0o600)
def wait_stack(cf):
    last=None
    for _ in range(100):
        stack=cf.describe_stacks(StackName=STACK)['Stacks'][0]
        status=stack['StackStatus']
        if status!=last:print('Stack:',status,flush=True);last=status
        if status in ('CREATE_COMPLETE','UPDATE_COMPLETE'):return stack
        if 'FAILED' in status or ('ROLLBACK' in status and not status.endswith('IN_PROGRESS')):
            events=cf.describe_stack_events(StackName=STACK)['StackEvents']
            for e in events:
                if 'FAILED' in e['ResourceStatus']:print(e['LogicalResourceId'],e.get('ResourceStatusReason'),flush=True)
            raise RuntimeError(status)
        time.sleep(10)
    raise TimeoutError('Stack still running; inspect before retrying')

def run(args):
    account=client('sts').get_caller_identity()['Account']
    print('Deploying to AWS account',account,'region',REGION,flush=True)
    # Every invocation is attributed to a tagged application inference profile.
    br=client('bedrock')
    profiles=br.list_inference_profiles(typeEquals='APPLICATION')['inferenceProfileSummaries']
    profile=next((p for p in profiles if p['inferenceProfileName']=='promptwerk'),None)
    if profile is None:
        profile=br.create_inference_profile(inferenceProfileName='promptwerk',modelSource={'copyFrom':f'arn:aws:bedrock:{REGION}::foundation-model/qwen.qwen3-32b-v1:0'},tags=TAGS and [{'key':'Project','value':'promptwerk'}])
    model=profile['inferenceProfileArn']
    ce=client('ce','us-east-1')
    activation=ce.update_cost_allocation_tags_status(CostAllocationTagsStatus=[{'TagKey':'Project','Status':'Active'}])
    if activation.get('Errors'):raise RuntimeError('Cost allocation tag could not be activated: '+str(activation['Errors']))
    print('Project cost allocation tag activated.',flush=True)
    waf=client('wafv2','us-east-1')
    acl=next((a for a in waf.list_web_acls(Scope='CLOUDFRONT')['WebACLs'] if a['Name']=='promptwerk-edge'),None)
    if acl is None:
        visibility={'SampledRequestsEnabled':False,'CloudWatchMetricsEnabled':False,'MetricName':'promptwerk'}
        acl=waf.create_web_acl(Name='promptwerk-edge',Scope='CLOUDFRONT',DefaultAction={'Allow':{}},Description='Promptwerk free-plan IP rate limit',Rules=[{'Name':'IpRateLimit','Priority':0,'Statement':{'RateBasedStatement':{'Limit':60,'EvaluationWindowSec':300,'AggregateKeyType':'IP'}},'Action':{'Block':{}},'VisibilityConfig':visibility}],VisibilityConfig=visibility,Tags=TAGS)['Summary']
    settings_path=STATE/'secrets.json'
    if settings_path.exists():settings=json.loads(settings_path.read_text())
    else:
        settings={'origin':secrets.token_urlsafe(40),'salt':secrets.token_urlsafe(40)}
        save('secrets.json',settings)
    cf=client('cloudformation')
    try:
        previous=cf.describe_stacks(StackName=STACK)['Stacks'][0]
    except ClientError as e:
        if 'does not exist' not in str(e):raise
        previous=None
    if previous and previous['StackStatus'].endswith('IN_PROGRESS'):
        previous=wait_stack(cf)
    concurrency=0
    if previous:
        fn=next(x['OutputValue'] for x in previous.get('Outputs',[]) if x['OutputKey']=='FunctionName')
        concurrency=client('lambda').get_function_concurrency(FunctionName=fn).get('ReservedConcurrentExecutions',0)
    parameters={'ModelId':model,'BudgetEmail':args.email,'WebAclArn':acl['ARN'],'Concurrency':str(concurrency),'OriginSecret':settings['origin'],'IpSalt':settings['salt'],'DomainName':args.domain,'CertificateArn':args.certificate,'HostedZoneId':args.zone}
    if bool(args.domain)!=bool(args.certificate):raise ValueError('Domain and us-east-1 certificate ARN must be provided together')
    body=json.dumps(template(),separators=(',',':'))
    (ROOT/'infra/template.json').write_text(json.dumps(template(),indent=2)+'\n')
    cf.validate_template(TemplateBody=body)
    kwargs={'StackName':STACK,'TemplateBody':body,'Parameters':[{'ParameterKey':k,'ParameterValue':v} for k,v in parameters.items()],'Capabilities':['CAPABILITY_IAM'],'Tags':TAGS}
    if previous:
        try:cf.update_stack(**kwargs)
        except ClientError as e:
            if 'No updates are to be performed' not in str(e):raise
    else:cf.create_stack(**kwargs)
    stack=wait_stack(cf)
    out={v['OutputKey']:v['OutputValue'] for v in stack['Outputs']}
    save('outputs.json',out)
    ppm=client('pricing-plan-manager','us-east-1')
    dist_arn=f'arn:aws:cloudfront::{account}:distribution/{out["DistributionId"]}'
    plans=ppm.list_subscriptions().get('subscriptionSummaries',[])
    plan=next((p for p in plans if dist_arn in p.get('resourceArns',[])),None)
    if plan is None:
        plan=ppm.create_subscription(planFamily='CloudFront',planTier='FREE',resourceArns=[dist_arn,acl['ARN']],clientToken=f'promptwerk-free-{out["DistributionId"]}')
        save('plan-create.json',plan)
        print('Free CloudFront subscription requested.',flush=True)
        plans=ppm.list_subscriptions().get('subscriptionSummaries',[])
        plan=next((p for p in plans if dist_arn in p.get('resourceArns',[])),None)
    for _ in range(30):
        if plan and plan['planTier']=='FREE' and plan['status']=='ACTIVE':break
        print('Waiting for FREE subscription activation.',flush=True)
        time.sleep(10)
        plans=ppm.list_subscriptions().get('subscriptionSummaries',[])
        plan=next((p for p in plans if dist_arn in p.get('resourceArns',[])),None)
    assert plan and plan['planTier']=='FREE' and plan['status']=='ACTIVE','Free plan not active: backend remains disabled'
    assert acl['ARN'] in plan['resourceArns'],'WAF must be covered by the FREE plan'
    save('plan.json',plan)
    print('CloudFront FREE plan verified.',flush=True)
    s3=client('s3')
    for path in (ROOT/'web').iterdir():
        if not path.is_file():continue
        mime=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        s3.put_object(Bucket=out['Bucket'],Key=path.name,Body=path.read_bytes(),ContentType=mime+'; charset=utf-8',CacheControl='public, max-age=300',ServerSideEncryption='AES256')
    client('cloudfront','us-east-1').create_invalidation(DistributionId=out['DistributionId'],InvalidationBatch={'Paths':{'Quantity':1,'Items':['/*']},'CallerReference':str(time.time_ns())})
    print('Static app uploaded.',flush=True)
    if args.enable and concurrency==0:
        parameters['Concurrency']='2'
        kwargs['Parameters']=[{'ParameterKey':k,'ParameterValue':v} for k,v in parameters.items()]
        cf.update_stack(**kwargs)
        wait_stack(cf)
        # Reconcile drift after a previous alarm, only with explicit --enable.
        client('lambda').put_function_concurrency(FunctionName=out['FunctionName'],ReservedConcurrentExecutions=2)
        print('Backend enabled.',flush=True)
    print('URL:',out['Url'],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--email',required=True)
    p.add_argument('--enable',action='store_true',help='Explicitly enable or resume model processing after safeguards are checked')
    p.add_argument('--domain',default='')
    p.add_argument('--certificate',default='',help='ACM certificate ARN in us-east-1')
    p.add_argument('--zone',default='',help='Optional existing Route 53 hosted zone id')
    run(p.parse_args())
