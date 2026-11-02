# Estado da modernização

## Entregue nesta etapa

- DCA SaaS structure: public portal, organization registration, one general plan, pending subscription, multiple specialties, customer dashboard, tenant users/roles and separate DCA Super Admin. See `DCA_SAAS.md`.
- No gateway, pricing or commercial limits are configured; no payment record or activation action exists. Legacy CNES adoption is intentionally held until a safe grandfathering flow is approved.

- Esquema aditivo com organizações, trilha mínima de auditoria, versões e hash SHA-256 de documentos, sem apagar tabelas ou registros existentes.
- Associação inicial de cada CNES a uma organização e tela administrativa para agrupar unidades sob uma organização. O agrupamento atualiza os vínculos organizacionais em uma transação; o escopo clínico continua filtrado por CNES.
- Controle de lotes, validade, fornecedor e histórico de movimentos de estoque por unidade.
- Configuração de referência para PostgreSQL, Redis, Gunicorn, Nginx, monitoramento e backup cifrado com age.
- Procedimento local em WSL2, SQLite para desenvolvimento e interface web responsiva existente para uso em navegadores móveis.
- Ferramenta de migração SQLite→PostgreSQL com origem somente leitura, validação de integridade e proteção contra destino preenchido.

## Pendências antes de uso clínico em produção

- Nenhum conector FHIR/RNDS/e-SUS está configurado. É necessário decidir versão e perfis FHIR, escopos, consentimentos, ambiente de homologação, credenciais e requisitos dos órgãos envolvidos.
- Prazos de retenção, exportação e exclusão precisam de política aprovada pelo controlador e assessoria responsável; não foi inventado prazo legal nem criada exclusão automática.
- A assinatura exibida em documentos não foi validada como assinatura eletrônica qualificada. Definir provedor/certificado, evidências e validação jurídica.
- Permissões administrativas ainda não são configuráveis por organização. O escopo operacional de usuários clínicos/farmácia é por CNES.
- Alertas configurados em painel não incluem canal de notificação externo. Definir destinatários e serviço de notificação.
- PostgreSQL, Docker, alertas, backups e restauração precisam ser exercitados em ambiente de homologação com credenciais e chave age fornecidas pelo operador.
- Executar avaliação de acessibilidade com leitor de tela/teclado, teste de carga representativo e análise de vulnerabilidades antes do lançamento.

## Restrições de implantação

As mudanças de esquema são aditivas. A ferramenta não apaga a origem SQLite e se recusa a importar para tabelas de destino com dados. Faça backup cifrado verificado antes da migração, teste a restauração em homologação e só então agende a migração de produção. Nenhuma integração externa ou serviço de produção foi iniciado nesta etapa.

## Validacoes adicionadas

- MFA TOTP obrigatorio para `DCA_SUPER_ADMIN`, com segredo cifrado, QR Code, codigos unicos de recuperacao, rejeicao de replay e bloqueio temporario persistido no banco.
- `requirements-test.txt` mantem pytest e pip-audit fora da imagem Docker e inclui as mesmas dependencias de runtime de `requirements.txt`.
- O daemon Docker nao estava disponivel nesta sessao; PostgreSQL, persistencia apos restart, backup e restauracao seguem pendentes de homologacao.
- O MFA implementado precisa de ensaio com autenticadores reais e procedimento operacional de recuperacao antes da producao.
