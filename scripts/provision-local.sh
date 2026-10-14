#!/usr/bin/env bash
set -euo pipefail

BUCKET_NAME="${MEDIA_BUCKET_NAME:-media-service-local}"
TABLE_NAME="${MEDIA_TABLE_NAME:-media-service-local}"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
FUNCTION_NAME="media-service-local"
ROLE_NAME="media-service-local-lambda-role"
API_NAME="media-service-local"
ROLE_ARN="arn:aws:iam::000000000000:role/${ROLE_NAME}"
LAMBDA_ARN="arn:aws:lambda:${REGION}:000000000000:function:${FUNCTION_NAME}"

localstack() {
  docker compose exec -T localstack awslocal "$@"
}

wait_for_lambda() {
  for attempt in $(seq 1 60); do
    state="$(localstack lambda get-function-configuration --function-name "$FUNCTION_NAME" --query State --output text 2>/dev/null || true)"
    update_status="$(localstack lambda get-function-configuration --function-name "$FUNCTION_NAME" --query LastUpdateStatus --output text 2>/dev/null || true)"
    if [ "$state" = "Active" ] && [ "$update_status" != "InProgress" ] && [ "$update_status" != "Failed" ]; then
      return 0
    fi
    sleep 1
  done

  echo "Lambda function did not become ready: ${FUNCTION_NAME}" >&2
  return 1
}

if ! localstack s3api head-bucket --bucket "$BUCKET_NAME" >/dev/null 2>&1; then
  localstack s3api create-bucket --bucket "$BUCKET_NAME"
fi
localstack s3api put-public-access-block \
  --bucket "$BUCKET_NAME" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
localstack s3api put-bucket-cors --bucket "$BUCKET_NAME" --cors-configuration \
  '{"CORSRules":[{"AllowedMethods":["GET","HEAD","POST"],"AllowedOrigins":["http://127.0.0.1:8000","http://localhost:8000","https://app.localstack.cloud","http://*.execute-api.localhost.localstack.cloud:4566","https://*.execute-api.localhost.localstack.cloud:4566"],"AllowedHeaders":["*"],"ExposeHeaders":["ETag"]}]}'

if ! localstack dynamodb describe-table --table-name "$TABLE_NAME" >/dev/null 2>&1; then
  localstack dynamodb create-table \
    --table-name "$TABLE_NAME" \
    --billing-mode PAY_PER_REQUEST \
    --attribute-definitions \
      AttributeName=media_id,AttributeType=S \
      AttributeName=owner_id,AttributeType=S \
      AttributeName=created_at,AttributeType=S \
    --key-schema AttributeName=media_id,KeyType=HASH \
    --global-secondary-indexes \
      'IndexName=owner-created-index,KeySchema=[{AttributeName=owner_id,KeyType=HASH},{AttributeName=created_at,KeyType=RANGE}],Projection={ProjectionType=ALL}'
fi

if ! localstack iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  localstack iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
fi
localstack iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name media-service-local-policy \
  --policy-document \
  "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:PutObject\",\"s3:DeleteObject\"],\"Resource\":\"arn:aws:s3:::${BUCKET_NAME}/*\"},{\"Effect\":\"Allow\",\"Action\":[\"dynamodb:GetItem\",\"dynamodb:PutItem\",\"dynamodb:UpdateItem\",\"dynamodb:DeleteItem\",\"dynamodb:Query\"],\"Resource\":\"arn:aws:dynamodb:${REGION}:000000000000:table/${TABLE_NAME}*\"},{\"Effect\":\"Allow\",\"Action\":[\"logs:CreateLogGroup\",\"logs:CreateLogStream\",\"logs:PutLogEvents\"],\"Resource\":\"*\"}]}"

if ! localstack lambda get-function --function-name "$FUNCTION_NAME" >/dev/null 2>&1; then
  localstack lambda create-function \
    --function-name "$FUNCTION_NAME" \
    --runtime python3.12 \
    --role "$ROLE_ARN" \
    --handler handler.handler \
    --timeout 30 \
    --zip-file fileb:///workspace/build/media-service.zip \
    --environment "Variables={AWS_ENDPOINT_URL=http://localstack:4566,AWS_PUBLIC_ENDPOINT_URL=http://localhost.localstack.cloud:4566,AWS_DEFAULT_REGION=${REGION},AWS_ACCESS_KEY_ID=test,AWS_SECRET_ACCESS_KEY=test,MEDIA_BUCKET_NAME=${BUCKET_NAME},MEDIA_TABLE_NAME=${TABLE_NAME}}"
else
  wait_for_lambda
  localstack lambda update-function-code \
    --function-name "$FUNCTION_NAME" \
    --zip-file fileb:///workspace/build/media-service.zip >/dev/null
fi

wait_for_lambda
for attempt in $(seq 1 10); do
  if localstack lambda update-function-configuration \
    --function-name "$FUNCTION_NAME" \
    --environment "Variables={AWS_ENDPOINT_URL=http://localstack:4566,AWS_PUBLIC_ENDPOINT_URL=http://localhost.localstack.cloud:4566,AWS_DEFAULT_REGION=${REGION},AWS_ACCESS_KEY_ID=test,AWS_SECRET_ACCESS_KEY=test,MEDIA_BUCKET_NAME=${BUCKET_NAME},MEDIA_TABLE_NAME=${TABLE_NAME}}" >/dev/null 2>&1; then
    break
  fi
  if [ "$attempt" = 10 ]; then
    echo "Could not update Lambda environment after waiting: ${FUNCTION_NAME}" >&2
    exit 1
  fi
  sleep 1
done
wait_for_lambda

API_ID="$(localstack apigatewayv2 get-apis --query "Items[?Name=='${API_NAME}'].ApiId | [0]" --output text)"
if [ "$API_ID" = "None" ] || [ -z "$API_ID" ]; then
  API_ID="$(localstack apigatewayv2 create-api --name "$API_NAME" --protocol-type HTTP --query ApiId --output text)"
fi

INTEGRATION_ID="$(localstack apigatewayv2 get-integrations --api-id "$API_ID" --query 'Items[0].IntegrationId' --output text)"
if [ "$INTEGRATION_ID" = "None" ] || [ -z "$INTEGRATION_ID" ]; then
  INTEGRATION_ID="$(localstack apigatewayv2 create-integration --api-id "$API_ID" --integration-type AWS_PROXY --integration-uri "$LAMBDA_ARN" --payload-format-version 2.0 --query IntegrationId --output text)"
fi

for ROUTE_KEY in 'ANY /' 'ANY /{proxy+}'; do
  ROUTE_ID="$(localstack apigatewayv2 get-routes --api-id "$API_ID" --query "Items[?RouteKey=='${ROUTE_KEY}'].RouteId | [0]" --output text)"
  if [ "$ROUTE_ID" = "None" ] || [ -z "$ROUTE_ID" ]; then
    localstack apigatewayv2 create-route --api-id "$API_ID" --route-key "$ROUTE_KEY" --target "integrations/${INTEGRATION_ID}" >/dev/null
  fi
done

if ! localstack apigatewayv2 get-stage --api-id "$API_ID" --stage-name '$default' >/dev/null 2>&1; then
  localstack apigatewayv2 create-stage --api-id "$API_ID" --stage-name '$default' --auto-deploy >/dev/null
fi

localstack lambda add-permission \
  --function-name "$FUNCTION_NAME" \
  --statement-id media-service-local-api \
  --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:${REGION}:000000000000:${API_ID}/*/*" >/dev/null 2>&1 || true

echo "LocalStack resources are ready."
echo "API Gateway API ID: $API_ID"
