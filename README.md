<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:061A2B,50:0B4F6C,100:14B8A6&height=220&section=header&text=DV%20Odonto&fontSize=55&fontColor=ffffff&animation=fadeIn&fontAlignY=35"/>

<img src="https://readme-typing-svg.demolab.com?font=Fira+Code&size=21&duration=2800&pause=900&color=5EEAD4&center=true&vCenter=true&width=850&lines=Gest%C3%A3o+odontol%C3%B3gica+moderna;Odontograma+digital+e+interativo;Prontu%C3%A1rio+%7C+Agenda+%7C+Financeiro;Tecnologia+para+modernizar+cl%C3%ADnicas+odontol%C3%B3gicas"/>

<br>

<img src="https://img.shields.io/badge/Status-Em%20Desenvolvimento-14B8A6?style=for-the-badge"/>
<img src="https://img.shields.io/badge/Projeto-DV%20Odonto-0B4F6C?style=for-the-badge"/>
<img src="https://img.shields.io/badge/License-Proprietary-334155?style=for-the-badge"/>

</div>

---

# 🦷 DV Odonto

**DV Odonto** é uma plataforma de gestão odontológica criada para centralizar informações clínicas e administrativas em uma experiência moderna, organizada e intuitiva.

A proposta é unir:

**Paciente + Prontuário + Odontograma + Agenda + Financeiro + Automação**

em uma única plataforma.

> 🦷 **Tecnologia para cuidar de sorrisos.**

---

# 🦷 Odontograma Digital

<div align="center">

<img src="./odontograma.svg" width="100%" alt="Odontograma DV Odonto"/>

</div>

### Sistema baseado na numeração FDI

O odontograma foi projetado para trabalhar com a dentição permanente utilizando a numeração internacional FDI:

```text
                 ARCADA SUPERIOR

        18 17 16 15 14 13 12 11
        21 22 23 24 25 26 27 28

        ───────── LINHA MÉDIA ─────────

        48 47 46 45 44 43 42 41
        31 32 33 34 35 36 37 38

                 ARCADA INFERIOR
```

### 🦷 Faces dentárias

O sistema considera as principais faces:

```text
        V = Vestibular
        L = Lingual
        M = Mesial
        D = Distal
        O = Oclusal
```

A ideia é permitir que o profissional selecione um dente, identifique a face e registre a condição ou procedimento correspondente.

---

# ⚡ Experiência do Odontograma

```text
       🦷 SELECIONAR DENTE
                ↓
       🔎 IDENTIFICAR FACE
                ↓
       📋 REGISTRAR CONDIÇÃO
                ↓
       🩺 REGISTRAR PROCEDIMENTO
                ↓
       💾 SALVAR NO PRONTUÁRIO
```

### Estados que podem ser representados

* 🟥 Cárie
* 🟦 Restauração
* 🟨 Tratamento endodôntico
* 🟪 Extração
* 🟩 Coroa / Prótese
* ⬜ Ausente
* 🟦 Selante
* 🟧 Outros procedimentos

---

# ✨ Principais módulos

<div align="center">

|   🦷 Odontograma  |    👥 Pacientes   |   📋 Prontuário   |
| :---------------: | :---------------: | :---------------: |
| Registro dentário | Cadastro completo | Histórico clínico |

| 📅 Agenda |    💰 Financeiro    | 📊 Dashboard |
| :-------: | :-----------------: | :----------: |
| Consultas | Controle financeiro |  Indicadores |

</div>

---

# 👥 Pacientes

O módulo de pacientes foi pensado para centralizar as informações necessárias para o atendimento.

```text
Paciente
   │
   ├── Dados pessoais
   ├── Anamnese
   ├── Histórico
   ├── Odontograma
   ├── Procedimentos
   ├── Documentos
   └── Histórico financeiro
```

---

# 📋 Prontuário Digital

O prontuário organiza o histórico do paciente e permite relacionar informações clínicas aos atendimentos realizados.

### Estrutura planejada

```text
PRONTUÁRIO
│
├── Identificação
├── Anamnese
├── Histórico clínico
├── Odontograma
├── Procedimentos
├── Evoluções
├── Documentos
└── Histórico de atendimentos
```

---

# 📅 Agenda

A agenda tem como objetivo facilitar a organização dos atendimentos da clínica.

```text
┌────────────────────────────────────┐
│              AGENDA                │
├────────────────────────────────────┤
│                                    │
│  08:00  ── Paciente                │
│  09:00  ── Paciente                │
│  10:00  ── Disponível              │
│  11:00  ── Paciente                │
│  14:00  ── Paciente                │
│                                    │
└────────────────────────────────────┘
```

---

# 💰 Financeiro

Módulo destinado ao gerenciamento financeiro da clínica.

### Recursos planejados

* 💵 Receitas
* 💳 Pagamentos
* 📄 Procedimentos
* 📊 Relatórios
* 📈 Indicadores
* 👤 Histórico financeiro do paciente

---

# 📱 Automação

A arquitetura do DV Odonto foi pensada para permitir integrações e automações.

```text
                  DV ODONTO
                      │
        ┌─────────────┼─────────────┐
        │             │             │
        ▼             ▼             ▼
     WhatsApp       E-mail         API
        │             │             │
        └─────────────┼─────────────┘
                      ▼
                  AUTOMAÇÕES
```

### Possibilidades

* 🔔 Lembretes de consulta
* 📅 Confirmação de atendimento
* 📱 Comunicação com pacientes
* 📄 Envio de documentos
* 🔄 Automações administrativas

---

# 🧠 Arquitetura

```text
                         DV ODONTO
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
          FRONTEND         BACKEND       DATABASE
              │              │              │
              ▼              ▼              ▼
         Interface          API        PostgreSQL
              │              │              │
              │        ┌─────┴─────┐         │
              │        │           │         │
              ▼        ▼           ▼         ▼
        Odontograma   Auth      Finance    Pacientes
                         │
                         ▼
                       Redis
                         │
                         ▼
                    Integrações
```

---

# 🛠️ Tecnologias

<div align="center">

<img src="https://skillicons.dev/icons?i=python,html,css,javascript,postgresql,redis,docker,git,github,linux&perline=5"/>

</div>

### Backend

* 🐍 Python
* 🔌 APIs
* 🔐 Autenticação
* 🗄️ PostgreSQL
* ⚡ Redis

### Frontend

* HTML
* CSS
* JavaScript
* Interface responsiva
* Componentes interativos

### Infraestrutura

* 🐳 Docker
* 🐧 Linux
* 🔧 Git
* 🐙 GitHub

---

# 🔐 Segurança

Por lidar com informações odontológicas, o projeto considera segurança e privacidade desde a arquitetura.

```text
🔐 Autenticação
       ↓
👥 Controle de acesso
       ↓
🗄️ Banco de dados
       ↓
🔒 Proteção das informações
       ↓
📋 Auditoria
       ↓
🇧🇷 LGPD
```

> A implementação definitiva dos requisitos de segurança e LGPD deverá ser validada antes de qualquer uso em produção.

---

# 📊 Visão do sistema

```text
┌─────────────────────────────────────────────┐
│                 DV ODONTO                   │
├─────────────────────────────────────────────┤
│                                             │
│  🦷 ODONTOGRAMA                             │
│                                             │
│  👥 PACIENTES                               │
│                                             │
│  📋 PRONTUÁRIO                              │
│                                             │
│  📅 AGENDA                                  │
│                                             │
│  💰 FINANCEIRO                              │
│                                             │
│  📄 DOCUMENTOS                              │
│                                             │
│  📊 DASHBOARD                               │
│                                             │
│  🔐 USUÁRIOS E PERMISSÕES                   │
│                                             │
│  📱 INTEGRAÇÕES                             │
│                                             │
└─────────────────────────────────────────────┘
```

---

# 🚧 Roadmap

### Fundação

* [x] Repositório
* [x] Identidade visual
* [x] Arquitetura inicial
* [ ] Estrutura definitiva do banco

### Odontograma

* [x] Numeração FDI
* [x] 32 dentes permanentes
* [x] Faces V / L / M / D / O
* [ ] Seleção de dente
* [ ] Registro de procedimentos
* [ ] Histórico por dente
* [ ] Odontograma conectado ao prontuário

### Gestão

* [ ] Pacientes
* [ ] Anamnese
* [ ] Prontuário
* [ ] Agenda
* [ ] Financeiro
* [ ] Dashboard
* [ ] Relatórios

### Integrações

* [ ] WhatsApp
* [ ] Notificações
* [ ] E-mail
* [ ] APIs externas

### Segurança

* [ ] Controle de permissões
* [ ] Auditoria
* [ ] Criptografia
* [ ] Backup
* [ ] Adequação para produção

---

# 🗂️ Estrutura do projeto

A estrutura planejada é semelhante a:

```text
DV-Odonto/
│
├── README.md
├── odontograma.svg
│
├── frontend/
│   ├── components/
│   ├── pages/
│   ├── styles/
│   └── assets/
│
├── backend/
│   ├── routes/
│   ├── services/
│   ├── models/
│   └── auth/
│
├── database/
│   ├── migrations/
│   └── schema.sql
│
├── docker/
│
└── docs/
```

---

# 🔒 Código-fonte

O **DV Odonto é um projeto proprietário**.

A apresentação pública deste repositório serve como portfólio e demonstração do projeto.

O código-fonte principal e componentes internos podem permanecer em um repositório privado.

```text
🟢 README / Demonstração
        │
        ▼
   Repositório Público

        +

🔒 Código da aplicação
        │
        ▼
   Repositório Privado
```

---

# 🎯 Visão

O objetivo do DV Odonto é construir uma experiência de gestão odontológica moderna, combinando:

```text
       🦷 ODONTOLOGIA
              +
       💻 TECNOLOGIA
              +
       🤖 AUTOMAÇÃO
              +
       📊 DADOS
              ↓
        🚀 DV ODONTO
```

---

# 🏥 Portal DV Saúde — estado atual

A aplicação deste repositório é Flask com SQLite (não Django/PostgreSQL). Para desenvolvimento
sem container, `python app.py` inicia o servidor de desenvolvimento; para execução containerizada,
use a seção Docker abaixo, que inicia o Gunicorn. O portal central está em `/` e preserva as
rotas odontológicas existentes.

| Área | Rotas | Estado implementado |
| --- | --- | --- |
| DV Prontuário | `/prontuario/`, `/prontuario` | Reutiliza o registro SOAP existente |
| DV Odonto | `/odonto/`, `/pacientes`, `/odontograma`, `/agenda`, `/documentos` | Área existente acessível pelo portal |
| DV Farmácia SUS | `/farmacia-sus/` e subrotas | Cadastro de medicamentos, lotes, entradas, saídas, dispensações e relatórios locais |
| DV Gestão Pública | `/gestao-publica/` | Cadastro básico de unidades, profissionais e indicadores existentes |
| DV Integrações | `/integracoes/` | Exibe conectores como não configurados |
| Busca global | `/busca/` | Busca limitada a nomes de pacientes e medicamentos; somente perfil ADMIN |

As rotas de aplicação exigem autenticação, com permissões clínicas, farmacêuticas e administrativas
para as áreas novas. As operações farmacêuticas exigem token CSRF, justificativa, quantidades
positivas e transação SQLite; movimentos concluídos permanecem no histórico e não podem ser apagados
pela interface. Administradores autenticados podem cadastrar contas em `/usuarios`; o cadastro web e
o endpoint `POST /api/auth/register` exigem perfil ADMIN e token CSRF. Senhas novas precisam ter no
mínimo 12 caracteres e são armazenadas com hash Werkzeug.

Para produção, configure `SECRET_KEY` com um valor aleatório forte e habilite
`SESSION_COOKIE_SECURE=true` somente quando o site estiver servido por HTTPS. Sem `SECRET_KEY`, a
aplicação recusa iniciar quando executada com `PORT` definido; em desenvolvimento local é usada uma
chave temporária, que invalida as sessões quando o processo reinicia.
O arquivo `render.yaml` configura o serviço do Render para gerar e armazenar `SECRET_KEY` como
variável secreta e ativar cookies seguros. Para um serviço Render já criado sem Blueprint, adicione
`SECRET_KEY` em **Dashboard → serviço → Environment** usando um valor aleatório de pelo menos 32
bytes, marque-o como secret e salve para disparar um novo deploy. Não use a expressão literal
`generateValue` como valor no painel, não publique a chave e mantenha a mesma chave entre deploys
para preservar as sessões existentes.
Em uma instalação vazia, defina `INITIAL_ADMIN_PASSWORD` (mínimo de 12 caracteres) antes da
primeira inicialização para criar a conta administrativa. Essa variável **não** altera contas
existentes. O administrador legado com a senha pública de bootstrap continua bloqueado no login;
para recuperar somente essa conta, use o comando interativo abaixo. Ele exige uma senha nova
digitada com entrada oculta, cria e valida um backup antes de alterar o hash, e recusa modificar a
conta se ela não corresponder exatamente ao estado legado. Nenhuma conta é criada ou substituída
durante a recuperação.

**Limitações importantes:** o banco atual não associa usuários a organizações/unidades. Até essa
estrutura de permissões ser implementada e testada, as novas áreas administrativas/farmacêuticas não
oferecem isolamento multiunidade e devem ser usadas apenas em ambiente controlado. Dispensações não
validam prescrições; não existem conectores validados para e-SUS APS, LEDI, RNDS, e-SUS AF ou Hórus.
O prontuário ainda não implementa deduplicação de pacientes, alergias/medicamentos estruturados,
exames ou encaminhamentos; a busca global cobre somente nomes de pacientes e medicamentos. O módulo
de farmácia não oferece edição de cadastro, exportação de relatórios nem vínculo da dispensação a
uma prescrição/paciente.
Não use o sistema para atendimento real ou produção até uma revisão de segurança, privacidade,
backup/restauração e autorização por unidade.

---

## Execução local com Docker Desktop e WSL 2

O container executa a aplicação Flask com Gunicorn, ligado a `0.0.0.0:8000` dentro do
container. No Windows, a porta fica limitada ao próprio computador em
`http://localhost:8000`. O Flask em modo de desenvolvimento (`python app.py`) não é usado
por este fluxo.

### Preparar e iniciar

Abra o PowerShell na pasta que contém este README. Crie `.env` uma única vez com uma chave
aleatória privada (não a publique nem a envie ao GitHub):

```powershell
if (-not (Get-Command docker.exe -ErrorAction SilentlyContinue)) {
    $dockerDir = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin'
    if (-not (Test-Path (Join-Path $dockerDir 'docker.exe'))) {
        throw "Docker CLI não encontrado. Verifique a instalação do Docker Desktop."
    }
    $env:PATH = "$dockerDir;$env:PATH"
}

if (-not (Test-Path .env)) {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
        $secret = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
        "SECRET_KEY=$secret" | Set-Content -Encoding ascii .env
    }
    finally {
        $rng.Dispose()
        Remove-Variable bytes, rng, secret -ErrorAction SilentlyContinue
    }
} else {
    Write-Host ".env já existe; mantendo a chave atual."
}
```

Não sobrescreva um `.env` existente; preserve a mesma `SECRET_KEY` entre reinicializações
para manter as sessões válidas. Em seguida, valide e construa antes de iniciar:

```powershell
New-Item -ItemType Directory -Force .\backups | Out-Null
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
Invoke-WebRequest -UseBasicParsing http://localhost:8000/login |
    Select-Object StatusCode
```

O último comando faz uma requisição HTTP real; o resultado esperado é `200`. O Compose
também marca o serviço como saudável após uma verificação HTTP em `/login`.

### Logs e parada

```powershell
docker compose logs -f web
docker compose stop
```

Para iniciar novamente, use `docker compose up -d`. Não use `docker compose down -v` nem
apague as pastas de dados.

### Recuperar o administrador legado e cadastrar usuários

Depois de reconstruir a imagem com as correções de autenticação, execute no PowerShell dentro da
pasta do projeto:

```powershell
docker compose exec web flask --app app recover-legacy-admin
```

Digite a nova senha (12 ou mais caracteres) duas vezes quando solicitado; a entrada fica oculta e
não deve ser enviada por chat nem gravada em comandos ou logs. O comando salva um snapshot SQLite
validado em `.\backups\` antes de atualizar exclusivamente o hash da conta `admin` legada. Se a
conta já não estiver no estado legado, o comando termina sem alterar contas. Em seguida, entre em
`http://localhost:8000/login` como `admin` usando a senha recém-definida. Acesse `/usuarios` com
essa conta para cadastrar outros usuários e atribuir perfis.

No modo local (sem Docker), pare o servidor antes da recuperação e execute o mesmo comando Flask
na pasta deste projeto:

```powershell
python -m flask --app app recover-legacy-admin
```

A senha nova é solicitada de forma oculta. Depois, reinicie `python app.py` na mesma pasta e entre
como `admin`; não use a senha de bootstrap antiga.

Se uma recuperação anterior já substituiu a senha legada, mas a senha digitada não estiver mais
disponível, redefina somente a conta administrativa pelo comando `reset-admin-password`, também
com o servidor parado:

```powershell
docker compose exec web flask --app app reset-admin-password
```

No modo local, use `python -m flask --app app reset-admin-password` na pasta do projeto. O comando
solicita a senha de forma oculta, cria um backup validado e altera apenas a conta `admin` com perfil
ADMIN; não cria contas nem altera usuários profissionais.

Para aplicar alterações de código sem remover os dados persistentes:

```powershell
docker compose config --quiet
docker compose build web
docker compose up -d web
docker compose ps
Invoke-WebRequest -UseBasicParsing http://localhost:8000/login |
    Select-Object StatusCode
```

### Persistência e backup

O banco principal usado pelo código é `dv_odonto.db` na raiz deste projeto: por padrão,
`database.py` resolve o caminho relativo ao próprio arquivo, independentemente da pasta de onde
o servidor é iniciado. É possível configurar um caminho absoluto alternativo com `DV_DATABASE_PATH`.
No container, esse caminho continua sendo `/app/dv_odonto.db`. O arquivo
existente em `data/dv_odonto.db` não é consultado pelo código atual, mas também é montado
para preservar essa cópia. Os anexos são gravados em `static/uploads`; `uploads` também é
preservada porque a aplicação cria essa pasta. Nenhum banco é recriado ou copiado para
substituir os arquivos existentes.

Para criar um snapshot consistente do banco SQLite enquanto o serviço está ativo e arquivar
os dados persistentes no Windows:

```powershell
docker compose exec -T web python -c "import sqlite3; from datetime import datetime; source=sqlite3.connect('/app/dv_odonto.db'); target=sqlite3.connect('/backups/dv_odonto-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.db'); source.backup(target); target.close(); source.close()"
$stamp = Get-Date -Format yyyyMMdd-HHmmss-fff
$databaseBackup = Get-ChildItem .\backups\dv_odonto-*.db |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 -ExpandProperty FullName
Compress-Archive -Path $databaseBackup, .\data\dv_odonto.db, .\static\uploads, .\uploads `
    -DestinationPath ".\backups\dv-odonto-files-$stamp.zip"
```

Guarde os arquivos de backup fora do computador e teste a restauração antes de depender
deles. Bancos e anexos podem conter dados pessoais e de saúde: não os publique em
repositórios. Este `.gitignore` não ignora o banco nem os uploads; revise cuidadosamente
antes de adicionar arquivos ao Git. SQLite é mantido nesta etapa e é adequado para uma
primeira execução local com um único worker; não é uma configuração para múltiplas réplicas
ou gravação concorrente em vários servidores. Antes de produção/nuvem, faça revisão de
segurança e privacidade, configure HTTPS (e então `SESSION_COOKIE_SECURE=true`),
backup/restauração e estratégia apropriada de banco de dados.

# 👨‍💻 Desenvolvedor

<div align="center">

### Davi do Carmo

**Estudante de Redes de Computadores • Desenvolvedor em formação**

<br>

<a href="https://github.com/davicarmomacarau-ux">
<img src="https://img.shields.io/badge/GitHub-Davi%20do%20Carmo-181717?style=for-the-badge&logo=github&logoColor=white"/>
</a>

<a href="https://www.linkedin.com/in/davi-carmo-30588a345">
<img src="https://img.shields.io/badge/LinkedIn-Davi%20do%20Carmo-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white"/>
</a>

</div>

---

<div align="center">

<img src="https://readme-typing-svg.demolab.com?font=Fira+Code&size=18&duration=2800&pause=900&color=14B8A6&center=true&vCenter=true&width=750&lines=Tecnologia+que+trabalha+ao+lado+do+profissional.;Gest%C3%A3o+mais+organizada.;Uma+nova+experi%C3%AAncia+para+cl%C3%ADnicas+odontol%C3%B3gicas.;DV+Odonto+%F0%9F%A6%B7"/>

<br><br>

⭐ **DV Odonto — Tecnologia para cuidar de sorrisos.**

<br><br>

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:14B8A6,50:0B4F6C,100:061A2B&height=130&section=footer&animation=fadeIn"/>

</div>
