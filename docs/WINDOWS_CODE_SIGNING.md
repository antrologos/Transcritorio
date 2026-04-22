# Code Signing no Windows — opções, custos e passo a passo

**Status**: backlog de decisão (2026-04-22). Não implementado.

Este documento registra o estado da arte de code signing para Windows em
2026 e avalia as opções disponíveis para o Transcritório, um projeto
acadêmico open-source (licença MIT) do IESP-UERJ/CERES, mantido por
pessoa física no Brasil. O objetivo é eliminar — ou pelo menos reduzir —
o aviso "**O Windows protegeu o computador — Fornecedor desconhecido**"
que o Microsoft Defender SmartScreen mostra ao executar o instalador.

O documento serve como referência para quando o autor decidir investir
no certificado. Está propositalmente detalhado porque o tema é
repleto de armadilhas (especialmente em 2024-2026, pós-revogação do
privilégio EV-imediato da Microsoft).

---

## 1. Por que o aviso aparece

O `.exe` distribuído em `Releases/v0.1.1/Transcritorio-0.1.1-Setup.exe` é
**ad-hoc signed** (Inno Setup não assina o installer sem `SignTool=`
configurado, e o pipeline atual não usa certificado). Para o Windows, ele
é indistinguível de malware novo: nenhum publisher conhecido, nenhuma
reputação, nenhuma cadeia de confiança.

O Microsoft Defender SmartScreen então dispara três camadas de proteção:

1. **Mark of the Web (MOTW)**: o arquivo baixado via browser recebe o
   atributo NTFS `Zone.Identifier=3`, sinalizando origem externa.
2. **SmartScreen application reputation check**: ao duplo-clicar, o
   Windows consulta a base de reputação da Microsoft (`SmartScreenUrl`)
   com o SHA256 do executável. Se o hash não tem reputação mínima, o
   diálogo azul "O Windows protegeu o computador" é mostrado.
3. **User's choice**: o usuário precisa clicar em "Mais informações" →
   "Executar assim mesmo". Cerca de **60-80% dos usuários abandonam** a
   instalação neste ponto (dados internos de várias CAs; Certum reporta
   métricas similares).

**Consequência prática**: cada `.exe` novo construído pelo CI tem hash
diferente, logo reputação zerada. Se o Transcritório publicar 3 releases
em 6 meses, cada uma reinicia a contagem — não há "reputação do app" ou
"reputação do publisher unsigned", apenas "reputação do hash específico".

Um certificado de code signing resolve isso associando o binário a uma
identidade verificada por uma Autoridade Certificadora (CA) que a
Microsoft confia. A partir daí, a reputação é acumulada por **publisher
identity** ({Subject CN + chave pública}), não mais por hash.

---

## 2. Mudanças críticas 2024-2026 que você precisa saber

Três mudanças recentes invalidam conselhos encontrados em tutoriais
antigos. Entender essas mudanças economiza tempo e dinheiro.

### 2.1 Microsoft removeu o "SmartScreen imediato" do EV (2024)

Até 2023, certificados **EV (Extended Validation)** davam reputação
imediata no SmartScreen — o usuário nunca via o aviso azul. Era o
diferencial que justificava o preço (~$300-500/ano vs ~$75-200/ano do
OV).

Em 2024, a Microsoft silenciosamente removeu esse privilégio. Hoje, OV
e EV têm **comportamento SmartScreen equivalente**: ambos começam com
reputação zerada e acumulam gradualmente. A única vantagem restante do
EV é o **hardware token obrigatório** (mais seguro contra roubo de
chave), não performance de SmartScreen.

**Fonte**: [Microsoft Q&A — Reputation with OV vs EV](https://learn.microsoft.com/en-us/answers/questions/417016/reputation-with-ov-certificates-and-are-ev-certifi)

**Implicação pro Transcritório**: gastar ~$500/ano em EV deixou de fazer
sentido. OV padrão tem o mesmo efeito.

### 2.2 Validade máxima reduzida para 459 dias (fev/2026)

O CA/B Forum (corpo que rege CAs comerciais) reduziu a validade máxima
de certificados de code signing de 3 anos para **459 dias** a partir de
fevereiro/2026. Na prática, isso força renovação anual para todo mundo.

Em combinação com a próxima mudança (reset de reputação), essa redução
pode ser dolorosa.

**Fonte**: [SSL Insights — Code signing validity cut to 460 days](https://sslinsights.com/code-signing-certificate-validity-reduced-460-days/)

### 2.3 Reputação SmartScreen atrelada a {Subject + chave pública}

A base de reputação da Microsoft indexa por **subject Common Name +
public key hash**, não por cert serial ou publisher organization.
Consequência:

- **Renovação reutilizando a mesma chave** (Certum smartcard permite;
  alguns providers forçam nova chave) **preserva a reputação**.
- **Nova chave** (obrigatória em alguns providers, ou se perder o
  smartcard, ou se renovar via outra CA) **reseta a reputação**.

**Mitigação**: ao assinar, **sempre adicione um timestamp RFC 3161**
(`signtool /tr http://timestamp.sectigo.com /td sha256`). Binários com
timestamp permanecem válidos mesmo após o cert expirar — o Windows só
verifica se o cert era válido no momento do timestamp.

### 2.4 Azure Trusted Signing tem restrição geográfica

Azure Trusted Signing (anteriormente "Trusted Signing Service", agora
"Azure Artifact Signing") foi anunciado como o "WSL do code signing": 
$9.99/mês, gerenciado pela Microsoft, cert rotacionado diariamente
(validade 24h), reputação acumula por publisher identity.

**Armadilha**: em 2025-2026, a GA pública está restrita a indivíduos
com documento oficial de **EUA, Canadá, Reino Unido, ou países da
União Europeia**. Brasil está fora. Afiliação institucional (IESP-UERJ)
não ajuda — o critério é nacionalidade do documento.

**Fonte**: [Trusted Signing is open for individual developers (Public Preview)](https://techcommunity.microsoft.com/blog/microsoft-security-blog/trusted-signing-is-now-open-for-individual-developers-to-sign-up-in-public-previ/4273554)

**Implicação pro Transcritório**: Azure Trusted Signing **não é opção**
enquanto o autor residir no Brasil.

---

## 3. Comparação das opções disponíveis em 2026

| Opção | Custo ano 1 | Custo ano 2+ | Hardware token | SmartScreen imediato | Disponível BR? | Oficial? |
|---|---|---|---|---|---|---|
| **Certum Open Source** | ~€69 | ~€29 | Sim (smartcard USB) | Não | Sim | **Sim** |
| **Certum SimplySign** (cloud) | ~€189 | ~€189 | Não | Não | Sim | **Sim** |
| **Sectigo Standard OV** | ~$211-279 | ~$211-279 | Sim (token ou HSM) | Não | Sim | **Sim** |
| **SSL.com Standard OV** | ~$249+ | ~$249+ | Sim | Não | Sim | **Sim** |
| **DigiCert Standard OV** | ~$474+ | ~$474+ | Sim | Não | Sim | **Sim** |
| **SSL.com EV** | ~$349+ | ~$349+ | Sim (FIPS 140-2) | **Não** (mudou em 2024) | Sim | **Sim** |
| **Sectigo EV** | ~$279+ | ~$279+ | Sim | **Não** (mudou em 2024) | Sim | **Sim** |
| **Azure Trusted Signing** | $120 (12 × $9.99) | $120 | Não (gerenciado) | Não | **Não** (GA não cobre BR) | **Sim** |
| **Microsoft WDSI submission** | $0 | $0 | N/A | Reduz, não elimina | Sim | **Sim** (mas não é cert) |
| **Self-signed / ad-hoc** | $0 | $0 | N/A | Não | Sim | Não (atual) |

Preços são de checkout público em outubro/2025 e podem flutuar. **Sempre
confirme no site do CA antes de comprar.**

---

## 4. Análise detalhada da recomendação: Certum Open Source

### 4.1 Por que Certum

Certum é um dos braços de Asseco Data Systems S.A., uma das maiores
empresas de TI polonesas (listada na bolsa de Varsóvia). O Certum está
no **Microsoft Trusted Root Program** desde 2004 — sua raiz está incluída
em todas as instalações Windows modernas, o que significa que
certificados emitidos por eles são automaticamente reconhecidos.

É usado por projetos open-source consolidados como **RubyInstaller**,
**Pidgin**, e vários mantenedores de toolchains Python/Ruby/Node. Não é
uma CA obscura; é a "CA econômica reconhecida" do mundo OSS europeu.

Para um projeto acadêmico com baixa frequência de releases (menos de 10
por ano) e sem orçamento explícito, é **a melhor relação custo-benefício
disponível em 2026**.

### 4.2 Requisitos e processo

**Quem pode comprar**: pessoa física residente em qualquer país que
Certum aceite (inclui Brasil). Não precisa CNPJ, afiliação universitária,
ou identidade corporativa. Apenas documento de identidade com foto.

**O que você recebe**:
- Certificado x.509 válido por 1 ano (renovável), usável no `signtool.exe`
  do Windows SDK.
- Smartcard USB pequeno (SafeNet eToken 5110 ou similar) com chave
  privada **não-exportável** (Microsoft exige HSM ou smartcard desde 2023).
- Leitor USB (se você não tiver um — vem no kit inicial).

**Processo completo (3-10 dias úteis)**:

1. **Compra online** em https://certum.store/open-source-code-signing-code.html
   - Produto: "Open Source Code Signing" (verifique o nome — Certum tem
     variações. Se vir "Standard Code Signing" ou "EV Code Signing" são
     produtos diferentes, mais caros).
   - Duração: 1 ano (mais barato, e com validade máxima de 459 dias em
     2026 não faz sentido comprar 3 anos).
   - Primeira compra: ~€69 (cert + smartcard + leitor USB + ~€35 DHL
     internacional).
   - **Confirme os preços no checkout antes de pagar.** A página pode
     estar desatualizada; use o checkout como fonte da verdade.

2. **Upload de documentos** (via portal web Certum).
   - RG ou passaporte (foto colorida, alta resolução).
   - Comprovante de endereço recente (conta de luz, água, fatura de
     cartão — até 3 meses).
   - Auto-retrato segurando o documento (algumas CAs pedem; confirmar
     no fluxo do Certum).

3. **Verificação** (3-7 dias úteis).
   - Certum pode ligar para o telefone fornecido (confirmando que é
     você). Atenda — se perder, você volta para a fila.
   - Pode mandar email pedindo esclarecimentos (traduzir documento
     brasileiro se estiver em português — normalmente aceitam português
     pra UE/Polônia, mas confirme).

4. **Envio do smartcard** (2-5 dias úteis via DHL internacional).
   - Tracking via DHL. Mais ou menos €35 no frete.
   - Alfândega brasileira: geralmente passa sem tributação porque o
     valor declarado é baixo (~€30 hardware) e o conteúdo é "software
     authentication device". Mas pode pedir até 60% de impostos em casos
     aleatórios. **Reserve ~R$ 300 de margem.**

5. **Instalação do smartcard** (1 hora).
   - Baixar driver SafeNet da página do Certum.
   - Plugar smartcard → reader → USB.
   - Instalar cert pessoal no Windows Certificate Store.
   - Testar com `signtool sign /sha1 <thumbprint> /fd sha256 /tr http://time.certum.pl /td sha256 arquivo.exe`

### 4.3 Alternativa sem hardware: Certum SimplySign

Se você não quer lidar com smartcard físico (perda, quebra, esquecer em
outro computador ao viajar), o Certum SimplySign é cloud signing — a
chave fica em HSM hospedado no datacenter Certum, e você autentica via
OTP/token mobile.

- **Custo**: ~€189/ano (vs ~€69 do smartcard).
- **Vantagem**: assina de qualquer máquina com internet; sem hardware
  para perder.
- **Desvantagem**: dependência de internet no momento do assinar; custo
  ~3x maior; se Certum cair, você não assina.

**Recomendação pro Transcritório**: smartcard físico. €69 vs €189 é
diferença grande para um projeto sem orçamento. O CI assina local (no
Windows runner do GitHub Actions), não precisa de cloud signing.

### 4.4 Integração no CI (GitHub Actions)

**Problema**: smartcard é hardware local. GitHub Actions roda em VMs
efêmeras — não dá pra plugar smartcard lá.

**Solução (padrão da indústria)**: exportar a chave pública para
criar CSR local; GitHub Actions importa o cert (público) via secret;
no momento de assinar, **o autor assina localmente** com smartcard e
faz upload do `.exe` assinado para o Release.

Fluxo concreto:

```yaml
# .github/workflows/release.yml (job build-windows-installer)

- name: Upload unsigned .exe as artifact
  uses: actions/upload-artifact@v4
  with:
    name: Transcritorio-windows-installer-unsigned
    path: installer-out/*.exe
```

Depois:

```powershell
# No computador do autor, após o CI concluir
gh run download <run-id> --name Transcritorio-windows-installer-unsigned --dir unsigned
signtool sign `
  /sha1 <thumbprint-do-cert-no-store> `
  /fd sha256 `
  /tr http://time.certum.pl `
  /td sha256 `
  /v `
  unsigned\Transcritorio-0.1.2-Setup.exe

# Verifica assinatura
signtool verify /pa /v unsigned\Transcritorio-0.1.2-Setup.exe

# Upload pro Release
gh release upload v0.1.2 unsigned\Transcritorio-0.1.2-Setup.exe --clobber
```

**Alternativa full-CI**: usar SimplySign (cloud). GitHub Actions roda
`signtool` contra o HSM da Certum, sem hardware físico. Custa €189/ano
(incremental sobre €69). Faz sentido se a cadência de releases for alta
(>15/ano) e assinar-local virar gargalo.

### 4.5 Checklist pós-compra

- [ ] Cert instalado no Windows Certificate Store (Personal → Certificates)
- [ ] Thumbprint anotado (SHA1) — é o que `signtool` referencia
- [ ] Driver SafeNet instalado e testado
- [ ] Timestamp server configurado (`http://time.certum.pl` é o oficial)
- [ ] `signtool.exe` do Windows SDK no PATH (normalmente em `C:\Program Files (x86)\Windows Kits\10\bin\x64\`)
- [ ] Build local de `.exe` + assinar + `signtool verify` → passa
- [ ] Testar em máquina limpa (idealmente Windows sandbox) — SmartScreen
      mostra o aviso com **"Fornecedor: Rogério Jerônimo Barbosa"**, não
      mais "Desconhecido". Reputação começa a acumular a partir daí.
- [ ] Atualizar `README.md` removendo o disclaimer sobre SmartScreen (ou
      mudar para "pode aparecer aviso nos primeiros downloads" em vez de
      sempre).

### 4.6 Manutenção anual

- **Renovação**: ~60 dias antes de expirar, Certum envia lembrete por
  email. Clicar renovar (reutiliza smartcard, portanto reutiliza chave
  = **preserva reputação SmartScreen**). Custo ~€29.
- **Revogação**: se perder o smartcard, reportar imediatamente ao
  Certum para revogar o cert. Comprar outro (novo kit ~€69, nova chave,
  reputação zerada).
- **Backup**: chave privada é non-exportable por design (smartcard HSM).
  Não tente copiar. Se perder, só o caminho de revogação funciona.

---

## 5. Alternativas e quando fazem sentido

### 5.1 Sectigo Standard OV (~$211/ano)

**Quando**: se você quer cloud signing desde o dia 1 (Sectigo inclui
eSigner) ou se não quiser esperar 3-10 dias pela entrega do smartcard
Certum.

**Armadilha**: o preço inicial em $211 pode esconder renewal a $279
(Sectigo é conhecido por reajuste agressivo). Confirmar no checkout.

**Fonte**: [Best Code Signing Providers 2026 — SSL Insights](https://sslinsights.com/best-code-signing-certificate-providers/)

### 5.2 SSL.com OV (~$249+)

**Quando**: você precisa de eSigner nativo (cloud signing da SSL.com,
integra bem com CI). Suporte em inglês é sólido.

**Armadilha**: SSL.com pivotou para forcar EV em planos de "software
publishing" — verificar se o Standard OV ainda está disponível a $249.

**Fonte**: [SSL.com Code Signing FAQs](https://www.ssl.com/faqs/which-code-signing-certificate-do-i-need-ev-ov/)

### 5.3 DigiCert (~$474+)

**Quando**: projeto tem orçamento e precisa de suporte corporativo
24/7. Para OSS sem orçamento, **não vale**.

### 5.4 Microsoft WDSI submission (gratuito)

O [Microsoft Windows Defender Security Intelligence](https://www.microsoft.com/wdsi/filesubmission)
aceita submissões de "Software Developer" para análise prévia de um
arquivo. Se o hash for confirmado como legítimo, entra em uma whitelist
que **reduz** o aviso SmartScreen (mas não elimina para usuários que
têm configurações mais restritivas).

**Como usar**:

1. Build do `.exe` (unsigned é aceito).
2. Ir em https://www.microsoft.com/wdsi/filesubmission.
3. Escolher "Software Developer" como role.
4. Upload do `.exe` + descrição: "Open-source desktop transcription app
   for Brazilian Portuguese interviews. MIT license. GitHub:
   antrologos/Transcritorio. Non-commercial academic use."
5. Aguardar 1-3 dias úteis para análise.

**Valor pro Transcritório**: como **complemento** ao cert Certum, não
substituto. Submeter a cada release acelera a whitelist.

### 5.5 Self-signed (continuar como está)

**Quando**: estado atual. Justifica-se se:
- O público-alvo é 100% técnico (pesquisadores que já vivem na linha de
  comando), confortável com "Executar assim mesmo".
- Volume de usuários é baixo (<100/ano) e o incômodo é aceitável.
- Não há orçamento nem para €69/ano.

**Como está documentado**: no README seção "Windows 10/11" e no site
pt/en home via OsSwitcher. Funciona, mas perde ~60-80% dos downloads
que abandonam no SmartScreen.

---

## 6. Erros comuns e armadilhas

### 6.1 Comprar cert de CA não-reconhecida

Existem CAs baratas (<$50) que **não estão no Microsoft Trusted Root
Program**. O certificado emitido não é reconhecido pelo Windows — o
aviso SmartScreen continua idêntico ao unsigned. Antes de comprar,
verificar se a CA está em:

- Microsoft Root Certificate Program: https://learn.microsoft.com/en-us/security/trusted-root/participants-list
- Se a CA não aparece nessa lista → **não comprar**.

**Lista de CAs sabidamente não-confiáveis para code signing em 2026**:
ComodoSSLstore (o Comodo antigo se renomeou para Sectigo; versões
residuais "ComodoSSLstore" vendem certs que confundem a verificação).
Checar sempre pelo nome na lista MS Root.

### 6.2 Esquecer o timestamp

Se você assinar sem `/tr http://time.certum.pl /td sha256`, o binário
fica **inválido no dia em que o cert expirar** — mesmo binários já
baixados por usuários começam a falhar validação.

Com timestamp, o Windows verifica "cert era válido no momento do
timestamp?" — mesmo expirado depois, o binário continua válido.

**Regra**: **nunca assinar sem timestamp.** Sem exceção.

### 6.3 Confundir "code signing" com "SSL/TLS"

São certificados diferentes. Um cert SSL/TLS não assina código, e um
cert de code signing não serve HTTPS. CAs vendem ambos, mas no checkout
confirme que é "Code Signing Certificate" (às vezes chamado "Software
Signing Certificate"). Um cert SSL de $8 é inútil para assinar `.exe`.

### 6.4 Achar que o cert resolve "tudo"

Mesmo com cert OV assinado, o SmartScreen **inicialmente ainda mostra**
o aviso "O Windows protegeu o computador" — mas com nome do publisher
("Rogério Jerônimo Barbosa") no lugar de "Desconhecido". O aviso
desaparece gradualmente conforme reputação acumula (tipicamente
centenas de downloads em 2-4 semanas).

**Para eliminar imediatamente**: só EV-imediato fazia isso, e foi
removido em 2024. Alternativa é **Microsoft Hardware Dev Center**
(antigo "Partner Center"), gratuito, mas requer enviar driver kernel
para análise Microsoft — overkill para app userspace.

### 6.5 Tentar "contornar" SmartScreen programaticamente

Não faça. Rodar `Unblock-File` ou manipular Zone.Identifier pra remover
o marker só funciona localmente. No computador do usuário o arquivo
continua sinalizado. Qualquer "solução criativa" quebra em Windows
futuro e vira pesadelo de suporte.

---

## 7. Decisão recomendada (2026-04-22)

### Curto prazo (próximos 3-6 meses)
**Status quo**: continuar sem cert. Documentar o aviso claramente no
README e no site (já feito). Publicar `sha256` dos artefatos em cada
Release para permitir verificação manual por usuários técnicos.

### Médio prazo (6-12 meses)
Quando o autor decidir investir ~R$ 400 (€69 + frete + margem DHL):
**Certum Open Source smartcard USB**. Renovação a ~R$ 160/ano
(preservando reputação via reutilização de chave). Assinar local após
cada CI build, fazer upload com `--clobber` pro Release.

### Longo prazo (>12 meses)
Se o volume de downloads crescer (>500/mês), avaliar Certum SimplySign
(€189/ano) para integrar assinatura ao CI e remover o passo manual
local.

### Fora de escopo
- **EV**: não vale mais (sem SmartScreen imediato desde 2024).
- **Azure Trusted Signing**: indisponível no Brasil.
- **Homebrew/Linux-style distribution**: já temos .dmg + AppImage;
  code signing Windows é ortogonal a essa discussão.

---

## 8. Referências

- [Certum Store — Open Source Code Signing](https://certum.store/open-source-code-signing-code.html) — página oficial do produto
- [Certum OSS review walkthrough](https://piers.rocks/2025/10/30/certum-open-source-code-sign.html) — passo a passo recente de um desenvolvedor
- [Certum OSS honest review](https://www.msz.it/a-cheap-code-signing-certificate-for-open-source-projects-by-certum-asseco-an-honest-review-walkthrough/) — segunda opinião
- [RubyInstaller CertumCodeSigning wiki](https://github.com/oneclick/rubyinstaller2/wiki/CertumCodeSigning) — config CI real de projeto OSS usando Certum
- [Microsoft Trusted Root Program participants](https://learn.microsoft.com/en-us/security/trusted-root/participants-list) — lista oficial de CAs confiáveis
- [Microsoft Q&A — Reputation OV vs EV](https://learn.microsoft.com/en-us/answers/questions/417016/reputation-with-ov-certificates-and-are-ev-certifi) — confirmação oficial da mudança EV 2024
- [Trusted Signing Public Preview for individuals](https://techcommunity.microsoft.com/blog/microsoft-security-blog/trusted-signing-is-now-open-for-individual-developers-to-sign-up-in-public-previ/4273554) — restrições geográficas
- [Azure Artifact Signing pricing](https://azure.microsoft.com/en-us/pricing/details/artifact-signing/) — preços oficiais
- [Rick Strahl — Setting up Microsoft Trusted Signing](https://weblog.west-wind.com/posts/2025/Jul/20/Fighting-through-Setting-up-Microsoft-Trusted-Signing) — experiência prática
- [SSL Insights — Best Code Signing Providers 2026](https://sslinsights.com/best-code-signing-certificate-providers/) — comparativo recente de preços
- [SSL Insights — Code signing validity reduced to 460 days](https://sslinsights.com/code-signing-certificate-validity-reduced-460-days/) — regra CA/B 2026
- [SSL.com Code Signing FAQs](https://www.ssl.com/faqs/which-code-signing-certificate-do-i-need-ev-ov/) — perguntas comuns EV vs OV
- [Microsoft WDSI file submission](https://www.microsoft.com/wdsi/filesubmission) — submissão para whitelist SmartScreen
- [Microsoft SmartScreen reputation docs](https://github.com/MicrosoftDocs/windows-dev-docs/blob/docs/hub/apps/package-and-deploy/smartscreen-reputation.md) — documentação técnica oficial

---

## Anexo A — Template de script de assinatura (para quando comprar)

```powershell
# sign-installer.ps1 — uso local após CI produzir o .exe não-assinado
#
# Pré-requisitos:
#   - Smartcard Certum plugado com driver SafeNet instalado
#   - signtool.exe no PATH (Windows SDK)
#   - Cert thumbprint conhecido: listar com
#       Get-ChildItem Cert:\CurrentUser\My

param(
  [Parameter(Mandatory=$true)][string]$ExePath,
  [Parameter(Mandatory=$true)][string]$Thumbprint
)

if (-not (Test-Path $ExePath)) {
  throw "Arquivo nao encontrado: $ExePath"
}

# Assinatura + timestamp RFC 3161 (essencial pra validade pós-expiracao)
signtool.exe sign `
  /sha1 $Thumbprint `
  /fd sha256 `
  /tr http://time.certum.pl `
  /td sha256 `
  /v `
  $ExePath

if ($LASTEXITCODE -ne 0) {
  throw "signtool.exe falhou com exit code $LASTEXITCODE"
}

# Verifica
signtool.exe verify /pa /v $ExePath
if ($LASTEXITCODE -ne 0) {
  throw "verify falhou — assinatura nao eh valida"
}

Write-Host "[ok] assinado + timestamp + verificado: $ExePath"
```

Uso:

```powershell
./sign-installer.ps1 -ExePath "D:\Downloads\Transcritorio-0.1.2-Setup.exe" -Thumbprint "ABCDEF0123456789..."
gh release upload v0.1.2 "D:\Downloads\Transcritorio-0.1.2-Setup.exe" --clobber
```

---

## Anexo B — O que falar para o usuário enquanto não há cert

Mensagem curta em README / site para contextualizar o aviso sem alarmar:

> **Aviso do Windows ao abrir o instalador.** O Windows Defender pode
> mostrar "O Windows protegeu o computador — Fornecedor desconhecido".
> Isso acontece porque o Transcritório ainda não usa um certificado de
> assinatura comercial (R$ 400-500/ano). **Não indica problema** com o
> arquivo — o código-fonte é público e auditável em
> https://github.com/antrologos/Transcritorio.
>
> Para continuar: clique em "Mais informações" → "Executar assim mesmo".
> Se preferir verificar a integridade do download, compare o sha256 do
> `.exe` com o publicado na página da Release.
