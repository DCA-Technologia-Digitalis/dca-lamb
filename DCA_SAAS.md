# DCA Software — estrutura SaaS

## Escopo disponível

- Portal público: `/dca/`; contratação cadastral: `/dca/cadastro`.
- Cada cadastro cria uma organização, responsável `ORG_ADMIN`, vínculos de especialidade, o **DCA SOFTWARE — PLANO GERAL** e uma assinatura `PENDING`, em uma única transação.
- O formulário coleta profissional individual/estabelecimento, especialidade principal e adicionais, profissionais previstos e unidades previstas. Organização pode cadastrar CNES e convidar profissionais a partir do painel do cliente.
- Painel do cliente: `/cliente/`; unidades: `/cliente/unidades`; usuários/perfis e convites: `/cliente/usuarios`.
- Painel comercial: `/dca/admin`. Ele apresenta metadados de clientes, estado das assinaturas, usuários organizacionais e módulos, e pode ajustar perfis profissionais dentro dos limites previstos. Não consulta tabelas clínicas. O perfil `DCA_SUPER_ADMIN` é separado do `ADMIN` clínico já existente.
- Catálogo integrado: DCA Médico, DCA Odonto, DCA Farma, DCA Gestão, DCA Prontuário, DCA Estoque e DCA Relatórios. As rotas apontam para funcionalidades DV existentes; não foram clonadas tabelas clínicas.

## Estado de cobrança e ativação

Não há SDK, credencial, webhook, cobrança ou pagamento simulado. O cadastro e a criação de usuários não ativam módulos clínicos. O Super Admin pode colocar uma solicitação em `UNDER_REVIEW` e habilitar/desabilitar entitlements, mas não existe ação de ativação comercial. A tabela de pagamentos começa vazia e só poderá ser alimentada quando um gateway real for aprovado e integrado com validação de webhook e idempotência.

Preço, periodicidade e limites comerciais não são definidos nesta versão. A renovação permanece em branco; o painel informa quando não há pagamentos. Não existe exclusão por inadimplência. Assinaturas e eventos têm histórico próprio.

## Criação do Super Admin

Defina estas variáveis no ambiente do servidor, usando um login novo, exclusivo e senha forte. O sistema cria o perfil separado no início da aplicação se a conta ainda não existir:

```text
DCA_SUPER_ADMIN_USER=login-comercial-exclusivo
DCA_SUPER_ADMIN_NAME=Nome do responsável
DCA_SUPER_ADMIN_PASSWORD=senha-aleatoria-com-ao-menos-14-caracteres
```

Não reutilize o administrador clínico e não salve credenciais no repositório. Se o login informado já pertencer a outro perfil, a aplicação falha de forma explícita em vez de converter uma conta existente.

## Isolamento e compatibilidade

- O cadastro comercial recebe `organization_id` e só administra unidades CNES da própria organização.
- A inclusão de profissionais valida o pertencimento do CNES e usa convites passkey de uso único.
- Rotas clínicas, pacientes, documentos, arquivos e farmácia ficam bloqueados enquanto a assinatura não estiver `ACTIVE`. `PAST_DUE`, `SUSPENDED` e `CANCELLED` também bloqueiam os módulos; `EXPIRED` e calculado para uma renovação vencida e não e gravado no CHECK existente. Como não existe gateway nem ativação administrativa nesta etapa, novas assinaturas permanecem pendentes.
- Contas preexistentes sem assinatura DCA mantêm as regras atuais, preservando o acesso legado. O `ADMIN` legado continua com permissões históricas; não foi convertido em Super Admin.
- CNES já cadastrados não são transferidos automaticamente para novas organizações SaaS: isso poderia bloquear sessões clínicas legadas enquanto a assinatura está pendente. A vinculação/adoção de clínicas existentes depende de fluxo de grandfathering e reconciliação aprovado, ainda pendente.
- A contagem de armazenamento soma tamanhos de arquivos registrados em `uploads` e não lê conteúdo dos documentos.

## Banco e implantação

O schema comercial é criado com tabelas aditivas durante a inicialização. As tabelas novas incluem catálogo de módulos/plano, perfil comercial da organização, especialidades, membros, assinatura única, eventos, pagamentos e auditoria comercial. O utilitário SQLite→PostgreSQL foi atualizado para incluir esses registros e continua exigindo destino vazio. Nenhuma migração ou importação de banco real foi executada.

Antes de produção, configure e teste um gateway escolhido pela DCA, formalize preço, periodicidade e limites, valide CNES conforme a regra operacional adotada e ensaie a migração/restauração em homologação. DCA Médico aponta para a tela SOAP clínica já existente, e DCA Prontuário para os cadastros/documentos de pacientes. A tela SOAP continua multiprofissional conforme os perfis atuais; fluxos médicos independentes não foram inventados nesta fase.

## MFA obrigatorio do Super Admin DCA

O primeiro login de `DCA_SUPER_ADMIN` exige configuracao de autenticador TOTP: QR Code ou chave manual, seguido de validacao no servidor. Dez codigos de recuperacao sao exibidos uma unica vez, armazenados apenas como hashes e consumidos no primeiro uso. Cinco tentativas invalidas bloqueiam novas tentativas por 15 minutos; um codigo TOTP aceito nao pode ser repetido. Nao existe opcao de desativar MFA pelo painel.

O segredo TOTP e cifrado no banco com uma chave derivada de `SECRET_KEY`. Preserve a mesma chave em todos os workers, backups e migracoes. A migracao SQLite para PostgreSQL inclui o segredo cifrado e os hashes de recuperacao; sem a `SECRET_KEY` original, nao sera possivel decifra-los. MFA ainda precisa de ensaio operacional com autenticadores reais antes de producao.

Rotas de pacientes, documentos, arquivos e APIs profissionais ficam bloqueadas enquanto a assinatura nao estiver `ACTIVE`. Estados `PAST_DUE`, `SUSPENDED` e `CANCELLED` bloqueiam os modulos. `EXPIRED` e calculado quando uma assinatura ativa tem renovacao vencida; ele nao e gravado no `CHECK` existente, evitando uma alteracao de esquema destrutiva.
