"""SNS budget/abuse alarm: disable ONLY the Promptwerk Lambda."""
import os
import boto3

def handler(event, context):
    if not event.get('Records'):
        raise ValueError('Expected SNS event')
    allowed=os.environ['TOPIC_ARN']
    if any(r.get('Sns',{}).get('TopicArn') != allowed for r in event['Records']):
        raise ValueError('Unexpected topic')
    boto3.client('lambda').put_function_concurrency(FunctionName=os.environ['FUNCTION_NAME'],ReservedConcurrentExecutions=0)
    return {'stopped':True}
