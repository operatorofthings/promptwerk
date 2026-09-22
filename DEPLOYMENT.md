# Deployment verification — 2026-09-22

Live app: https://d2mahxwp14of9s.cloudfront.net

- CloudFormation stack `promptwerk`, region `eu-central-1`.
- Model: Qwen3 32B through a tagged application inference profile, Frankfurt.
- CloudFront FREE subscription active; distribution and WAF both included.
- Cost allocation tag `Project` verified active.
- Monthly project budget: 6 USD; email thresholds at 3, 5, 6 USD;
  final threshold also publishes to the automatic stop topic.
- Six local tests passed, including fail-closed limits, UTF-8 validation,
  IPv6 grouping and output schema validation.
- Live DynamoDB checks verified the IP and monthly limits and confirmed that
  rejected requests do not partially consume other counters. Isolated test
  counters were removed afterward.
- Direct Lambda URL access returned HTTP 403.
- Live SNS shutdown test set backend concurrency to zero. Processing was
  explicitly restored to two after the test.
- Browser end-to-end refinement and clipboard copy succeeded. Mobile layout
  checked at 390 × 844.

Domain registration is outside the deployment. No custom domain is configured;
the user will provide the domain and DNS details later. Domain support is already
parameterized in the CloudFormation template.

Limitations: budget delivery itself cannot be tested by fabricating AWS spending;
notification configuration was verified and its SNS-to-Lambda shutdown path was
tested live. AWS billing data can lag. This is not a guarantee that the entire AWS
account's bill will remain below a fixed amount. See README for scope and controls.
