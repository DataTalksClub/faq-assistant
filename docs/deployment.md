# Deployment

The worker is an AWS Lambda (`python3.14`, arm64) behind a Function URL, defined
in `template.yaml` and deployed with AWS SAM. Pushes to `main` deploy
automatically via GitHub Actions (`.github/workflows/deploy.yml`) using a
least-privilege role assumed through GitHub OIDC — no long-lived AWS keys.

```
push to main ─▶ unit tests ─▶ build search index ─▶ assume OIDC role ─▶ sam deploy
```

## Prerequisites (one-time per AWS account)

These must exist before CI can deploy. They are the same for dev and production —
only the account differs.

### 1. GitHub OIDC provider

The account needs an IAM OIDC identity provider for GitHub Actions so the
workflow can assume a role without stored keys:

- Provider URL: `https://token.actions.githubusercontent.com`
- Audience: `sts.amazonaws.com`

Check / create:

```bash
aws iam list-open-id-connect-providers
# if absent:
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com
```

### 2. Bootstrap deploy (elevated credentials, once)

The scoped deploy role is part of this stack (`GitHubDeployRole`), so the *first*
deploy must use credentials that can create it (an admin/elevated session, not
the deploy role itself). From the repo root:

```bash
make index                      # build artifacts/search/search-index.zsx
uv run sam build
uv run sam deploy --guided \    # writes samconfig.toml on first run
  --parameter-overrides OpenAIApiKey=$OPENAI_API_KEY SharedSecret=$FAQ_ASSISTANT_SHARED_SECRET
```

`samconfig.toml` is already committed (stack `faq-assistant`, region `eu-west-1`,
`resolve_s3`, `CAPABILITY_IAM`), so after the first run plain `make deploy` works
locally too. The stack outputs:

- `FunctionUrl` — the public HTTPS endpoint.
- `DeployRoleArn` — the role CI assumes (needed for the secret below).

### 3. GitHub repository secrets

Set these on the repo (`gh secret set NAME --body ...` or **Settings → Secrets and
variables → Actions**):

| Secret | Value |
| --- | --- |
| `AWS_REGION` | `eu-west-1` |
| `AWS_DEPLOY_ROLE_ARN` | the `DeployRoleArn` stack output |
| `OPENAI_API_KEY` | OpenAI API key |
| `FAQ_ASSISTANT_SHARED_SECRET` | shared secret callers send in `x-faq-assistant-secret` |

```bash
aws cloudformation describe-stacks --stack-name faq-assistant --region eu-west-1 \
  --query "Stacks[0].Outputs[?OutputKey=='DeployRoleArn'].OutputValue" --output text
```

After this, **pushes to `main` deploy automatically** and no one runs `sam deploy`
by hand. The daily `rebuild-index` workflow uses the same secrets to refresh the
corpus + index and redeploy.

## The deploy role (least privilege)

`GitHubDeployRole` in `template.yaml` has **no AdministratorAccess**. Its inline
policy grants exactly what `sam deploy` of this stack needs:

- CloudFormation on the `faq-assistant` stack, the SAM-managed
  `aws-sam-cli-managed-default` stack (for `--resolve-s3`), and the Serverless
  transform.
- S3 on the SAM deploy bucket (`aws-sam-cli-managed-default-*`).
- Lambda on the `faq-assistant` function and its Function URL.
- IAM on `faq-assistant-*` roles, with `PassRole` restricted to
  `lambda.amazonaws.com`.

Its trust policy only allows `repo:DataTalksClub/faq-assistant:*` (via the
`GitHubRepo` template parameter) to assume it.

## Porting to a new / production account

1. Ensure the GitHub OIDC provider exists (step 1).
2. Bootstrap deploy once with elevated credentials (step 2) — this creates the
   function **and** the scoped deploy role.
3. Set the four secrets, pointing `AWS_DEPLOY_ROLE_ARN` at the new stack's output
   (and override `GitHubRepo` if the repo differs).

No template changes and no privilege escalation — the role is identical across
environments.

## Local commands

```bash
make test     # offline unit tests (mocked OpenAI + index)
make check    # config compile + unit tests + index build + handler smoke + compileall
make index    # build the packed search index
make deploy   # build index + sam build + sam deploy (uses local credentials)
```
