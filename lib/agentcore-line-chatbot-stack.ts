import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as apigateway from "aws-cdk-lib/aws-apigateway";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as agentcore from "@aws-cdk/aws-bedrock-agentcore-alpha";
import { Construct } from "constructs";

export class AgentcoreLineChatbotStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ========================================
    // AgentCore Runtime
    // ========================================
    const runtime = new agentcore.Runtime(this, "ChatbotAgentRuntime", {
      runtimeName: "agentcore_line_chatbot",
      agentRuntimeArtifact: agentcore.AgentRuntimeArtifact.fromAsset(
        path.join(__dirname, "../agent")
      ),
      networkConfiguration:
        agentcore.RuntimeNetworkConfiguration.usingPublicNetwork(),
      environmentVariables: {
        MODEL_ID: process.env.MODEL_ID || "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        TAVILY_API_KEY: process.env.TAVILY_API_KEY || "",
        AGENT_OBSERVABILITY_ENABLED: "true",
        OTEL_PYTHON_DISTRO: "aws_distro",
        OTEL_PYTHON_CONFIGURATOR: "aws_configurator",
        OTEL_EXPORTER_OTLP_PROTOCOL: "http/protobuf",
      },
    });

    // Bedrock モデル呼び出し権限
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
        ],
        resources: [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:*:*:inference-profile/*",
        ],
      })
    );

    // ========================================
    // Lambda (Webhook Handler + SSE Bridge)
    // ========================================
    const webhookFn = new lambda.Function(this, "WebhookFunction", {
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      handler: "webhook.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../lambda"), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_13.bundlingImage,
          platform: "linux/arm64",
          command: [
            "bash",
            "-c",
            "pip install -r requirements.txt -t /asset-output && cp *.py /asset-output",
          ],
        },
      }),
      timeout: cdk.Duration.seconds(120),
      memorySize: 256,
      environment: {
        LINE_CHANNEL_SECRET: process.env.LINE_CHANNEL_SECRET || "",
        LINE_CHANNEL_ACCESS_TOKEN:
          process.env.LINE_CHANNEL_ACCESS_TOKEN || "",
        AGENTCORE_RUNTIME_ARN: runtime.agentRuntimeArn,
      },
    });

    // Lambda → AgentCore 呼び出し権限
    runtime.grantInvokeRuntime(webhookFn);

    // ========================================
    // API Gateway (REST API - 非同期 Lambda 呼び出し)
    // ========================================
    const api = new apigateway.RestApi(this, "WebhookApi", {
      restApiName: "agentcore-line-chatbot-webhook",
      description: "LINE Webhook endpoint for AgentCore LINE Chatbot",
    });

    // API Gateway → Lambda 非同期呼び出し用ロール
    const apiGatewayRole = new iam.Role(this, "ApiGatewayLambdaRole", {
      assumedBy: new iam.ServicePrincipal("apigateway.amazonaws.com"),
    });
    webhookFn.grantInvoke(apiGatewayRole);

    // Lambda 非同期呼び出し統合
    const lambdaIntegration = new apigateway.AwsIntegration({
      service: "lambda",
      path: `2015-03-31/functions/${webhookFn.functionArn}/invocations`,
      integrationHttpMethod: "POST",
      options: {
        credentialsRole: apiGatewayRole,
        requestParameters: {
          "integration.request.header.X-Amz-Invocation-Type": "'Event'",
        },
        requestTemplates: {
          "application/json": `{
  "body": "$util.escapeJavaScript($input.body)",
  "signature": "$input.params('x-line-signature')"
}`,
        },
        integrationResponses: [
          {
            statusCode: "200",
            responseTemplates: {
              "application/json": '{"message": "accepted"}',
            },
          },
        ],
      },
    });

    const webhook = api.root.addResource("webhook");
    webhook.addMethod("POST", lambdaIntegration, {
      methodResponses: [{ statusCode: "200" }],
    });

    // ========================================
    // Outputs
    // ========================================
    new cdk.CfnOutput(this, "WebhookUrl", {
      value: `${api.url}webhook`,
      description: "LINE Webhook URL",
    });

    new cdk.CfnOutput(this, "AgentRuntimeArn", {
      value: runtime.agentRuntimeArn,
      description: "AgentCore Runtime ARN",
    });
  }
}
