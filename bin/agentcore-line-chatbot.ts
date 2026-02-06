#!/usr/bin/env node
import "source-map-support/register";
import * as dotenv from "dotenv";
import * as cdk from "aws-cdk-lib";
import { AgentcoreLineChatbotStack } from "../lib/agentcore-line-chatbot-stack";

dotenv.config({ path: ".env.local" });

const app = new cdk.App();
new AgentcoreLineChatbotStack(app, "AgentcoreLineChatbotStack", {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: "us-east-1",
  },
});
