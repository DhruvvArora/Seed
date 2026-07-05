# Renders step_function/backfill.asl.json, substituting the four
# ${...FunctionArn} placeholders with real Lambda ARNs via templatefile(),
# and deploys the state machine. The ASL file lives outside terraform/ so it
# can be validated on its own (statelint) independent of any Terraform run.

resource "aws_sfn_state_machine" "backfill" {
  name     = "${var.env}-mci-backfill"
  role_arn = aws_iam_role.backfill_state_machine.arn

  definition = templatefile("${path.module}/../step_function/backfill.asl.json", {
    ListTenantsFunctionArn       = aws_lambda_function.list_tenants.arn
    IterationUtilityFunctionArn  = aws_lambda_function.iteration_utility.arn
    AthenaQueryRunnerFunctionArn = aws_lambda_function.athena_query_runner.arn
    BackfillMciFunctionArn       = aws_lambda_function.backfill_mci.arn
  })

  tags = var.tags
}
