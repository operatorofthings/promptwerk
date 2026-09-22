"""Generate the reviewable CloudFormation template; no AWS calls here."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def ref(name):return {'Ref':name}
def att(name,attr):return {'Fn::GetAtt':[name,attr]}
def sub(value):return {'Fn::Sub':value}
TAGS=[{'Key':'Project','Value':'promptwerk'}]

def template():
    r={}
    def add(name,kind,props,**extra):r[name]={'Type':'AWS::'+kind,'Properties':props,**extra}
    def policy(statements):return {'Version':'2012-10-17','Statement':statements}
    trust=policy([{'Effect':'Allow','Principal':{'Service':'lambda.amazonaws.com'},'Action':'sts:AssumeRole'}])
    add('Assets','S3::Bucket',{'PublicAccessBlockConfiguration':{'BlockPublicAcls':True,'BlockPublicPolicy':True,'IgnorePublicAcls':True,'RestrictPublicBuckets':True},'BucketEncryption':{'ServerSideEncryptionConfiguration':[{'ServerSideEncryptionByDefault':{'SSEAlgorithm':'AES256'}}]},'Tags':TAGS},DeletionPolicy='Retain',UpdateReplacePolicy='Retain')
    add('Counters','DynamoDB::Table',{'AttributeDefinitions':[{'AttributeName':'pk','AttributeType':'S'}],'KeySchema':[{'AttributeName':'pk','KeyType':'HASH'}],'BillingMode':'PAY_PER_REQUEST','OnDemandThroughput':{'MaxReadRequestUnits':5,'MaxWriteRequestUnits':5},'TimeToLiveSpecification':{'AttributeName':'expires','Enabled':True},'Tags':TAGS},DeletionPolicy='Retain',UpdateReplacePolicy='Retain')
    add('AppLog','Logs::LogGroup',{'LogGroupName':sub('/aws/lambda/${AWS::StackName}-refine'),'RetentionInDays':3,'Tags':TAGS})
    add('AppRole','IAM::Role',{'AssumeRolePolicyDocument':trust,'Policies':[{'PolicyName':'PromptwerkOnly','PolicyDocument':policy([
        {'Effect':'Allow','Action':['logs:CreateLogStream','logs:PutLogEvents'],'Resource':att('AppLog','Arn')},
        {'Effect':'Allow','Action':['dynamodb:UpdateItem'],'Resource':att('Counters','Arn')},
        {'Effect':'Allow','Action':['bedrock:InvokeModel'],'Resource':[ref('ModelId'),sub('arn:${AWS::Partition}:bedrock:${AWS::Region}::foundation-model/qwen.qwen3-32b-v1:0')]}
    ])}],'Tags':TAGS})
    add('App','Lambda::Function',{'FunctionName':sub('${AWS::StackName}-refine'),'Runtime':'python3.12','Handler':'index.handler','Role':att('AppRole','Arn'),'Timeout':55,'MemorySize':256,'ReservedConcurrentExecutions':ref('Concurrency'),'Code':{'ZipFile':(ROOT/'backend/app.py').read_text()},'Environment':{'Variables':{'TABLE_NAME':ref('Counters'),'MODEL_ID':ref('ModelId'),'ORIGIN_SECRET':ref('OriginSecret'),'IP_SALT':ref('IpSalt')}},'Tags':TAGS},DependsOn=['AppLog'])
    add('AppUrl','Lambda::Url',{'TargetFunctionArn':att('App','Arn'),'AuthType':'AWS_IAM'})
    add('UrlInvokePermission','Lambda::Permission',{'FunctionName':ref('App'),'Action':'lambda:InvokeFunctionUrl','Principal':'cloudfront.amazonaws.com','SourceArn':sub('arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${Distribution}')})
    add('FunctionInvokePermission','Lambda::Permission',{'FunctionName':ref('App'),'Action':'lambda:InvokeFunction','Principal':'cloudfront.amazonaws.com','SourceArn':sub('arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${Distribution}'),'InvokedViaFunctionUrl':True})
    for name,kind in [('S3Access','s3'),('LambdaAccess','lambda')]:
        add(name,'CloudFront::OriginAccessControl',{'OriginAccessControlConfig':{'Name':sub('${AWS::StackName}-'+kind),'OriginAccessControlOriginType':kind,'SigningBehavior':'always','SigningProtocol':'sigv4'}})
    edge='''function handler(event) {
var r=event.request;
if(r.uri==='/api/refine' && r.method==='POST'){
r.headers['x-promptwerk-ip']={value:event.viewer.ip}; return r;
}
if((r.method==='GET'||r.method==='HEAD') && ['/','/index.html','/style.css','/app.js','/favicon.svg'].indexOf(r.uri)>=0){return r;}
return {statusCode:404,statusDescription:'Not Found',headers:{'cache-control':{value:'public, max-age=60'}}};
}'''
    add('Edge','CloudFront::Function',{'Name':sub('${AWS::StackName}-request'),'AutoPublish':True,'FunctionConfig':{'Comment':'Allow only app assets and refine route; overwrite trusted client IP','Runtime':'cloudfront-js-2.0'},'FunctionCode':edge})
    association=[{'EventType':'viewer-request','FunctionARN':att('Edge','FunctionARN')}]
    managed_security='67f7725c-6f97-4210-82d7-5512b31e9d03'
    add('Distribution','CloudFront::Distribution',{'DistributionConfig':{
        'Enabled':True,'Comment':'Promptwerk public app','DefaultRootObject':'index.html','HttpVersion':'http2and3','IPV6Enabled':True,'WebACLId':ref('WebAclArn'),
        'Aliases':{'Fn::If':['HasDomain',[ref('DomainName')],ref('AWS::NoValue')]},
        'ViewerCertificate':{'Fn::If':['HasDomain',{'AcmCertificateArn':ref('CertificateArn'),'SslSupportMethod':'sni-only','MinimumProtocolVersion':'TLSv1.2_2021'},{'CloudFrontDefaultCertificate':True}]},
        'Origins':[
            {'Id':'assets','DomainName':att('Assets','RegionalDomainName'),'S3OriginConfig':{'OriginAccessIdentity':''},'OriginAccessControlId':ref('S3Access')},
            {'Id':'api','DomainName':{'Fn::Select':[2,{'Fn::Split':['/',att('AppUrl','FunctionUrl')]}]},'CustomOriginConfig':{'OriginProtocolPolicy':'https-only','OriginSSLProtocols':['TLSv1.2'],'OriginReadTimeout':60},'OriginAccessControlId':ref('LambdaAccess'),'OriginCustomHeaders':[{'HeaderName':'X-Promptwerk-Origin','HeaderValue':ref('OriginSecret')}]}
        ],
        'DefaultCacheBehavior':{'TargetOriginId':'assets','ViewerProtocolPolicy':'redirect-to-https','AllowedMethods':['GET','HEAD'],'Compress':True,'CachePolicyId':'658327ea-f89d-4fab-a63d-7e88639e58f6','ResponseHeadersPolicyId':managed_security,'FunctionAssociations':association},
        'CacheBehaviors':[{'PathPattern':'/api/*','TargetOriginId':'api','ViewerProtocolPolicy':'https-only','AllowedMethods':['GET','HEAD','OPTIONS','PUT','PATCH','POST','DELETE'],'Compress':True,'CachePolicyId':'4135ea2d-6df8-44a3-9df3-4b5a84be39ad','OriginRequestPolicyId':'b689b0a8-53d0-40ab-baf2-68738e2966ac','ResponseHeadersPolicyId':managed_security,'FunctionAssociations':association}]
    },'Tags':TAGS})
    add('AssetsPolicy','S3::BucketPolicy',{'Bucket':ref('Assets'),'PolicyDocument':policy([
        {'Effect':'Allow','Principal':{'Service':'cloudfront.amazonaws.com'},'Action':'s3:GetObject','Resource':sub('${Assets.Arn}/*'),'Condition':{'StringEquals':{'AWS:SourceArn':sub('arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${Distribution}')}}},
        {'Effect':'Deny','Principal':'*','Action':'s3:*','Resource':[att('Assets','Arn'),sub('${Assets.Arn}/*')],'Condition':{'Bool':{'aws:SecureTransport':'false'}}}
    ])})
    add('StopTopic','SNS::Topic',{'Tags':TAGS})
    add('TopicPolicy','SNS::TopicPolicy',{'Topics':[ref('StopTopic')],'PolicyDocument':policy([{'Effect':'Allow','Principal':{'Service':['budgets.amazonaws.com','cloudwatch.amazonaws.com']},'Action':'sns:Publish','Resource':ref('StopTopic'),'Condition':{'StringEquals':{'aws:SourceAccount':ref('AWS::AccountId')}}}])})
    add('StopRole','IAM::Role',{'AssumeRolePolicyDocument':trust,'Policies':[{'PolicyName':'StopPromptwerkOnly','PolicyDocument':policy([{'Effect':'Allow','Action':'lambda:PutFunctionConcurrency','Resource':att('App','Arn')}])}],'Tags':TAGS})
    add('Stop','Lambda::Function',{'Runtime':'python3.12','Handler':'index.handler','Role':att('StopRole','Arn'),'Timeout':10,'MemorySize':128,'Code':{'ZipFile':(ROOT/'backend/stop.py').read_text()},'Environment':{'Variables':{'FUNCTION_NAME':ref('App'),'TOPIC_ARN':ref('StopTopic')}},'Tags':TAGS})
    add('StopPermission','Lambda::Permission',{'FunctionName':ref('Stop'),'Action':'lambda:InvokeFunction','Principal':'sns.amazonaws.com','SourceArn':ref('StopTopic')})
    add('StopSubscription','SNS::Subscription',{'TopicArn':ref('StopTopic'),'Protocol':'lambda','Endpoint':att('Stop','Arn')},DependsOn=['StopPermission'])
    notifications=[]
    for amount in [3,5,6]:
        subscribers=[{'SubscriptionType':'EMAIL','Address':ref('BudgetEmail')}]
        if amount==6:subscribers.append({'SubscriptionType':'SNS','Address':ref('StopTopic')})
        notifications.append({'Notification':{'NotificationType':'ACTUAL','ComparisonOperator':'GREATER_THAN','Threshold':amount,'ThresholdType':'ABSOLUTE_VALUE'},'Subscribers':subscribers})
    add('Budget','Budgets::Budget',{'Budget':{'BudgetName':sub('${AWS::StackName}-monthly'),'BudgetType':'COST','TimeUnit':'MONTHLY','BudgetLimit':{'Amount':6,'Unit':'USD'},'CostFilters':{'TagKeyValue':['user:Project$promptwerk']},'CostTypes':{'IncludeTax':True,'IncludeCredit':False,'IncludeRefund':False}},'NotificationsWithSubscribers':notifications},DependsOn=['TopicPolicy','StopSubscription'])
    add('TrafficAlarm','CloudWatch::Alarm',{'AlarmDescription':'Stop Promptwerk on unusual backend traffic (100 requests in 5 minutes). Manual resume required.','Namespace':'AWS/Lambda','MetricName':'Invocations','Dimensions':[{'Name':'FunctionName','Value':ref('App')}],'Statistic':'Sum','Period':300,'EvaluationPeriods':1,'Threshold':100,'ComparisonOperator':'GreaterThanOrEqualToThreshold','TreatMissingData':'notBreaching','AlarmActions':[ref('StopTopic')],'Tags':TAGS})
    add('DomainDns','Route53::RecordSetGroup',{'HostedZoneId':ref('HostedZoneId'),'RecordSets':[{'Name':ref('DomainName'),'Type':t,'AliasTarget':{'DNSName':att('Distribution','DomainName'),'HostedZoneId':'Z2FDTNDATAQYW2'}} for t in ['A','AAAA']]},Condition='HasDns')
    return {'AWSTemplateFormatVersion':'2010-09-09','Description':'Promptwerk: public prompt refinement with atomic quotas and fail-closed budget protection.',
        'Parameters':{'ModelId':{'Type':'String'},'BudgetEmail':{'Type':'String'},'WebAclArn':{'Type':'String'},'Concurrency':{'Type':'Number','Default':0,'AllowedValues':[0,2]},'OriginSecret':{'Type':'String','NoEcho':True},'IpSalt':{'Type':'String','NoEcho':True},'DomainName':{'Type':'String','Default':''},'CertificateArn':{'Type':'String','Default':''},'HostedZoneId':{'Type':'String','Default':''}},
        'Conditions':{
            'HasDomain':{'Fn::Not':[{'Fn::Equals':[ref('DomainName'),'']}]},
            'HasDns':{'Fn::And':[
                {'Fn::Not':[{'Fn::Equals':[ref('DomainName'),'']}]},
                {'Fn::Not':[{'Fn::Equals':[ref('HostedZoneId'),'']}]}
            ]}
        },
        'Resources':r,'Outputs':{name:{'Value':value} for name,value in {'Url':sub('https://${Distribution.DomainName}'),'DistributionId':ref('Distribution'),'Bucket':ref('Assets'),'FunctionName':ref('App'),'FunctionUrl':att('AppUrl','FunctionUrl'),'Table':ref('Counters'),'StopTopicArn':ref('StopTopic'),'StopFunction':ref('Stop'),'EdgeFunction':ref('Edge')}.items()}}

if __name__=='__main__':
    output=ROOT/'infra/template.json'
    output.write_text(json.dumps(template(),indent=2)+'\n')
    print(output)
