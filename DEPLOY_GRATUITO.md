# Hidrara — Deploy Gratuito (Demonstração)

Configuração escolhida: **tudo no Render.com** (backend + frontend, mesma
conta/painel) + **MongoDB Atlas** apenas para o banco de dados (o Render não
oferece MongoDB gratuito, então o banco fica no serviço oficial do Mongo,
que também é 100% grátis). Custo total: **R$ 0**.

| Camada    | Serviço            | Free tier                         |
|-----------|--------------------|------------------------------------|
| Frontend  | Render (Static Site) | ilimitado, mesma conta do backend |
| Backend   | Render (Web Service) | 750h/mês grátis (dorme após 15 min inativo) |
| Banco     | MongoDB Atlas M0   | 512 MB grátis, para sempre         |
| Imagens   | Disco local (padrão) ou Cloudflare R2 | R2: 10 GB grátis |

## O que foi alterado neste pacote

1. **Object Storage desacoplado da Emergent** — `backend/server.py` tem um
   backend de storage plugável (`STORAGE_BACKEND=local` ou `s3`), sem
   chamadas para `integrations.emergentagent.com`. Por padrão grava as
   imagens em `backend/uploads/` (bom para demo).
2. Removida a dependência `emergentintegrations` do `requirements.txt`.
3. `render.yaml` na raiz do projeto define **os dois serviços** (API +
   frontend estático) como um único Blueprint — você cria tudo de uma vez
   dentro da mesma conta Render.
4. `backend/Procfile`, `backend/.env.example`, `frontend/.env.example`.

## Passo a passo

### 1. Banco de dados — MongoDB Atlas (5 min, único serviço fora do Render)
1. Crie conta grátis em https://www.mongodb.com/atlas
2. Crie um cluster **M0 (Free)**.
3. Em *Database Access*, crie um usuário com senha.
4. Em *Network Access*, libere `0.0.0.0/0` (qualquer IP) para o demo.
5. Copie a connection string (`mongodb+srv://...`) — vai na variável `MONGO_URL`.

> Por que não dá pra fugir 100% de um segundo serviço: o Render não tem
> plano gratuito de banco MongoDB (só Postgres). O Atlas M0 é gratuito
> permanentemente e leva ~3 minutos para configurar — é a única etapa fora
> do painel do Render.

### 2. Tudo no Render — Backend + Frontend (Blueprint, ~5 min)
1. Suba este repositório para o seu GitHub.
2. Em https://render.com → **New +** → **Blueprint** → selecione o repo.
   O Render lê o `render.yaml` da raiz e cria automaticamente:
   - `hidrara-api` (Web Service Python — backend)
   - `hidrara-frontend` (Static Site — frontend React)
3. Preencha as variáveis pedidas (marcadas `sync: false` no blueprint):
   - No **hidrara-api**: `MONGO_URL` (do Atlas), `ADMIN_PASSWORD`,
     `CORS_ORIGINS` (cole a URL do `hidrara-frontend`, ex.:
     `https://hidrara-frontend.onrender.com`).
   - No **hidrara-frontend**: `REACT_APP_BACKEND_URL` (cole a URL do
     `hidrara-api`, ex.: `https://hidrara-api.onrender.com`).
4. Clique em **Apply**. Em poucos minutos os dois serviços sobem.
5. Teste o backend em `https://hidrara-api.onrender.com/api/` e o site em
   `https://hidrara-frontend.onrender.com`.

> Dica: como as duas URLs só existem depois do primeiro deploy, é normal
> fazer o primeiro deploy, copiar as URLs geradas e então voltar em
> **Environment** de cada serviço para preencher `CORS_ORIGINS` e
> `REACT_APP_BACKEND_URL` corretamente, disparando um redeploy.

### 3. Login administrativo
Acesse `/admin` no site do Render com o e-mail/senha definidos em
`ADMIN_EMAIL` / `ADMIN_PASSWORD`. O catálogo (24 produtos) e as categorias
são semeados automaticamente na primeira execução do backend.

### 4. (Opcional) Imagens persistentes — Cloudflare R2
O plano free do Render tem **disco efêmero** (perde arquivos de upload a
cada redeploy/restart do backend). Para upload de imagens realmente
persistente e ainda grátis:
1. Crie um bucket em https://dash.cloudflare.com → R2 (10 GB grátis).
2. Gere um Token de API S3-compatível (Access Key / Secret Key).
3. No serviço `hidrara-api` do Render, defina:
   ```
   STORAGE_BACKEND=s3
   S3_BUCKET=nome-do-bucket
   S3_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
   S3_REGION=auto
   S3_ACCESS_KEY_ID=...
   S3_SECRET_ACCESS_KEY=...
   ```
Sem essas variáveis, o backend usa disco local automaticamente — ótimo
para uma demonstração rápida.

### 5. Plano free do Render "dorme"
O Web Service gratuito hiberna após ~15 min sem acessos e leva ~30-50s
para acordar na próxima requisição. Normal em ambiente de demonstração;
não afeta o Static Site do frontend (esse não dorme).

## Checklist de variáveis de ambiente (backend)
Veja `backend/.env.example` para a lista completa comentada.

## Migração de dados (se já tiver produtos cadastrados em outro ambiente)
```
mongodump --uri="<MONGO_URL_ANTIGO>" --archive=hidrara.dump
mongorestore --uri="<MONGO_URL_NOVO>" --archive=hidrara.dump
```

Para mais detalhes de arquitetura, endpoints e modelos de dados, consulte
`Hidrara_Manual_Instalacao_e_Documentacao.pdf` incluído neste pacote.
