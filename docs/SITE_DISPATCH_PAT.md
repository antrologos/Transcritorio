# Como configurar `SITE_DISPATCH_PAT`

O workflow `release.yml` dispara um rebuild do site antrologos.github.io
quando uma nova release publica. Para isso ele precisa de um Personal
Access Token (PAT) com permissao de escrita naquele outro repositorio.

## Passo-a-passo

### 1. Criar o PAT (GitHub)

1. Entre em https://github.com/settings/tokens?type=beta
2. Clique em **Generate new token** → **Fine-grained token**
3. Campos:
   - **Token name**: `transcritorio-site-dispatch`
   - **Expiration**: **90 days** (ou menos; renovavel)
   - **Resource owner**: `antrologos`
   - **Repository access**: *Only select repositories* → marcar
     `antrologos/antrologos.github.io` (apenas este!)
   - **Repository permissions**:
     - **Contents**: `Read and write` (deixa os demais como `No access`)
     - **Metadata**: `Read-only` (sempre obrigatorio, ja vem marcado)
4. Clique em **Generate token**.
5. **Copie o valor** — ele aparece so uma vez (`github_pat_xxxxxxxx...`).

### 2. Salvar no repo Transcritorio como secret

1. Entre em https://github.com/antrologos/Transcritorio/settings/secrets/actions
2. Clique em **New repository secret**.
3. Campos:
   - **Name**: `SITE_DISPATCH_PAT`
   - **Secret**: cole o token.
4. Clique em **Add secret**.

### 3. Validar

Na proxima release (push de tag `v*`), o job `publish-release` vai
encontrar o secret e disparar o rebuild do site. Voce pode testar
manualmente:

```bash
# Substitua XXXX pelo valor real do PAT (apenas para testar)
curl -X POST \
  -H "Authorization: Bearer XXXX" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/antrologos/antrologos.github.io/dispatches \
  -d '{"event_type":"transcritorio-release","client_payload":{"version":"0.1.2"}}'
```

Se o resultado for HTTP 204 sem corpo, funcionou. Veja a aba **Actions**
do repo antrologos.github.io para confirmar que o workflow `Build and
deploy site` rodou.

## Em caso de esquecer / PAT expirar

Se o PAT expirar ou nao existir, o workflow `release.yml` **nao falha** —
apenas emite um warning. Voce pode rebuildar o site manualmente:

1. Entre em https://github.com/antrologos/antrologos.github.io/actions
2. Selecione o workflow **Build and deploy site**.
3. Clique em **Run workflow** → informe a versao (ex: `0.1.2`) → **Run workflow**.

Ou, mais simples, no proprio site-src:

```bash
cd site-src
npm run update-version   # busca a ultima release do GitHub
npm run build
git add ..
git commit -m "site: rebuild para vX.Y.Z"
git push
```
