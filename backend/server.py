from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

import os
import uuid
import logging
import bcrypt
import jwt
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Literal

try:
    import boto3
    from botocore.client import Config as BotoConfig
except ImportError:  # boto3 optional, only needed for S3/R2 storage backend
    boto3 = None

from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response, UploadFile, File, Query, Header
from fastapi.responses import Response as FastAPIResponse
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr


# --- Config ---
JWT_ALGORITHM = "HS256"
JWT_SECRET = os.environ["JWT_SECRET"]
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@hidrara.com.br")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Hidrara@2026")
APP_NAME = os.environ.get("APP_NAME", "hidrara")

# --- Storage backend selection ---
# STORAGE_BACKEND: "local" (default, writes to disk - fine for demo/small volumes),
#                  "s3" (AWS S3 / Cloudflare R2 / Backblaze B2 - any S3-compatible service)
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local").lower()
LOCAL_STORAGE_DIR = Path(os.environ.get("LOCAL_STORAGE_DIR", str(ROOT_DIR / "uploads")))
S3_BUCKET = os.environ.get("S3_BUCKET")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")  # e.g. Cloudflare R2 endpoint
S3_REGION = os.environ.get("S3_REGION", "auto")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY_ID")
S3_SECRET_KEY = os.environ.get("S3_SECRET_ACCESS_KEY")

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI(title="Hidrara API")
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# --- Storage helpers (pluggable backend: local disk or S3-compatible) ---
_s3_client = None


def _get_s3_client():
    global _s3_client
    if _s3_client is not None:
        return _s3_client
    if boto3 is None:
        raise HTTPException(status_code=500, detail="boto3 não instalado; adicione boto3 ao requirements.txt")
    if not (S3_BUCKET and S3_ACCESS_KEY and S3_SECRET_KEY):
        raise HTTPException(status_code=500, detail="Variáveis S3_BUCKET/S3_ACCESS_KEY_ID/S3_SECRET_ACCESS_KEY não configuradas")
    _s3_client = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        region_name=S3_REGION,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=BotoConfig(signature_version="s3v4"),
    )
    return _s3_client


def init_storage():
    """Kept for backward compatibility with startup hook; validates backend config."""
    if STORAGE_BACKEND == "local":
        LOCAL_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"Local disk storage ready at {LOCAL_STORAGE_DIR}")
        return "local"
    if STORAGE_BACKEND == "s3":
        try:
            _get_s3_client()
            logger.info(f"S3-compatible storage ready (bucket={S3_BUCKET})")
            return "s3"
        except HTTPException as e:
            logger.error(f"Storage init failed: {e.detail}")
            return None
    logger.error(f"Unknown STORAGE_BACKEND '{STORAGE_BACKEND}'")
    return None


def put_object(path: str, data: bytes, content_type: str) -> dict:
    if STORAGE_BACKEND == "s3":
        client_s3 = _get_s3_client()
        client_s3.put_object(Bucket=S3_BUCKET, Key=path, Body=data, ContentType=content_type)
        return {"path": path, "size": len(data)}
    # local backend (default)
    full_path = LOCAL_STORAGE_DIR / path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_bytes(data)
    return {"path": path, "size": len(data)}


def get_object(path: str):
    if STORAGE_BACKEND == "s3":
        client_s3 = _get_s3_client()
        try:
            obj = client_s3.get_object(Bucket=S3_BUCKET, Key=path)
        except Exception:
            raise HTTPException(status_code=404, detail="Arquivo não encontrado no storage")
        return obj["Body"].read(), obj.get("ContentType", "application/octet-stream")
    # local backend (default)
    full_path = LOCAL_STORAGE_DIR / path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado no storage")
    return full_path.read_bytes(), "application/octet-stream"


# --- Auth helpers ---
def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id, "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=8),
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
        "type": "refresh",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Não autenticado")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Token inválido")
        user = await db.users.find_one({"id": payload["sub"]})
        if not user:
            raise HTTPException(status_code=401, detail="Usuário não encontrado")
        user.pop("password_hash", None)
        user.pop("_id", None)
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Sessão expirada")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")


# --- Models ---
class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    email: EmailStr
    name: str
    role: str


class CategoryIn(BaseModel):
    name: str
    slug: str


class CategoryOut(CategoryIn):
    id: str


class ProductBase(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=2, max_length=200)
    category: str = Field(..., min_length=1)
    description: str = Field(default="", max_length=4000)
    image_url: str = Field(default="")
    brand: str = Field(default="")
    stock: Literal["available", "on_request", "out"] = "available"
    featured: bool = False


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    brand: Optional[str] = None
    stock: Optional[Literal["available", "on_request", "out"]] = None
    featured: Optional[bool] = None


class ProductOut(ProductBase):
    id: str
    created_at: str
    updated_at: str


class ContactIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    phone: str = Field(default="", max_length=40)
    email: EmailStr
    message: str = Field(..., min_length=5, max_length=4000)


class ContactOut(ContactIn):
    id: str
    read: bool
    created_at: str


# --- Auth routes ---
@api_router.post("/auth/login")
async def login(payload: LoginIn, response: Response):
    email = payload.email.lower().strip()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciais inválidas")
    access = create_access_token(user["id"], user["email"])
    refresh = create_refresh_token(user["id"])
    response.set_cookie("access_token", access, httponly=True, secure=True, samesite="none", max_age=8*3600, path="/")
    response.set_cookie("refresh_token", refresh, httponly=True, secure=True, samesite="none", max_age=7*86400, path="/")
    return {
        "id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"],
        "access_token": access,
    }


@api_router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"ok": True}


@api_router.get("/auth/me", response_model=UserOut)
async def me(user: dict = Depends(get_current_user)):
    return {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"]}


# --- Categories ---
@api_router.get("/categories", response_model=List[CategoryOut])
async def list_categories():
    cats = await db.categories.find({}, {"_id": 0}).sort("name", 1).to_list(200)
    return cats


@api_router.post("/categories", response_model=CategoryOut)
async def create_category(body: CategoryIn, user: dict = Depends(get_current_user)):
    exists = await db.categories.find_one({"slug": body.slug})
    if exists:
        raise HTTPException(status_code=400, detail="Categoria já existe")
    cat = {"id": str(uuid.uuid4()), **body.model_dump()}
    await db.categories.insert_one(cat)
    cat.pop("_id", None)
    return cat


@api_router.delete("/categories/{cat_id}")
async def delete_category(cat_id: str, user: dict = Depends(get_current_user)):
    await db.categories.delete_one({"id": cat_id})
    return {"ok": True}


# --- Products ---
@api_router.get("/products", response_model=List[ProductOut])
async def list_products(
    q: Optional[str] = Query(None, description="Buscar por nome ou código"),
    category: Optional[str] = Query(None, description="Slug da categoria"),
    featured: Optional[bool] = None,
):
    filt: dict = {}
    if category and category != "all":
        filt["category"] = category
    if featured is not None:
        filt["featured"] = featured
    if q:
        q_esc = q.strip()
        filt["$or"] = [
            {"name": {"$regex": q_esc, "$options": "i"}},
            {"code": {"$regex": q_esc, "$options": "i"}},
            {"description": {"$regex": q_esc, "$options": "i"}},
        ]
    products = await db.products.find(filt, {"_id": 0}).sort("created_at", -1).to_list(2000)
    return products


@api_router.get("/products/{product_id}", response_model=ProductOut)
async def get_product(product_id: str):
    p = await db.products.find_one({"id": product_id}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    return p


@api_router.post("/products", response_model=ProductOut)
async def create_product(body: ProductCreate, user: dict = Depends(get_current_user)):
    code = body.code.strip().upper()
    exists = await db.products.find_one({"code": code})
    if exists:
        raise HTTPException(status_code=400, detail=f"Código {code} já cadastrado")
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        **body.model_dump(),
        "code": code,
        "created_at": now,
        "updated_at": now,
    }
    await db.products.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api_router.put("/products/{product_id}", response_model=ProductOut)
async def update_product(product_id: str, body: ProductUpdate, user: dict = Depends(get_current_user)):
    p = await db.products.find_one({"id": product_id})
    if not p:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    update = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "code" in update:
        update["code"] = update["code"].strip().upper()
        dup = await db.products.find_one({"code": update["code"], "id": {"$ne": product_id}})
        if dup:
            raise HTTPException(status_code=400, detail="Código já em uso")
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.products.update_one({"id": product_id}, {"$set": update})
    p = await db.products.find_one({"id": product_id}, {"_id": 0})
    return p


@api_router.delete("/products/{product_id}")
async def delete_product(product_id: str, user: dict = Depends(get_current_user)):
    result = await db.products.delete_one({"id": product_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    return {"ok": True}


# --- Upload ---
@api_router.post("/upload")
async def upload_image(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    ext = (file.filename.split(".")[-1] if "." in (file.filename or "") else "bin").lower()
    if ext not in {"jpg", "jpeg", "png", "webp", "gif"}:
        raise HTTPException(status_code=400, detail="Formato de imagem inválido")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Arquivo excede 10MB")
    path = f"{APP_NAME}/products/{user['id']}/{uuid.uuid4()}.{ext}"
    ct = file.content_type or f"image/{'jpeg' if ext == 'jpg' else ext}"
    result = put_object(path, data, ct)
    await db.files.insert_one({
        "id": str(uuid.uuid4()),
        "storage_path": result["path"],
        "original_filename": file.filename,
        "content_type": ct,
        "size": result.get("size", len(data)),
        "is_deleted": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # Public URL routed through our backend
    return {"path": result["path"], "url": f"/api/files/{result['path']}"}


@api_router.get("/files/{path:path}")
async def download_file(path: str):
    record = await db.files.find_one({"storage_path": path, "is_deleted": False})
    if not record:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    data, ct = get_object(path)
    return FastAPIResponse(content=data, media_type=record.get("content_type", ct))


# --- Contact messages ---
@api_router.post("/contact")
async def submit_contact(body: ContactIn):
    doc = {
        "id": str(uuid.uuid4()),
        **body.model_dump(),
        "email": body.email.lower().strip(),
        "read": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.messages.insert_one(doc)
    logger.info(f"New contact message from {doc['email']}")
    return {"ok": True, "id": doc["id"]}


@api_router.get("/contact", response_model=List[ContactOut])
async def list_messages(user: dict = Depends(get_current_user)):
    msgs = await db.messages.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return msgs


@api_router.patch("/contact/{msg_id}/read")
async def mark_read(msg_id: str, user: dict = Depends(get_current_user)):
    await db.messages.update_one({"id": msg_id}, {"$set": {"read": True}})
    return {"ok": True}


@api_router.delete("/contact/{msg_id}")
async def delete_message(msg_id: str, user: dict = Depends(get_current_user)):
    await db.messages.delete_one({"id": msg_id})
    return {"ok": True}


# --- Site content (company info) ---
@api_router.get("/site/info")
async def site_info():
    return {
        "company": "Hidrara Conexões e Equipamentos Hidráulicos",
        "founded_city": "Araraquara",
        "founded_state": "SP",
        "founded_year": 1991,
        "years": datetime.now(timezone.utc).year - 1991,
        "phone_commercial": "(16) 3508-1300",
        "phone_sales": "(16) 3508-1300",
        "whatsapp": "551635081300",
        "email": "contato@hidrara.com.br",
        "partners": ["Parker Hannifin", "Mann-Filter", "Donaldson Company, Inc."],
        "units_count": 8,
        "mission": "Promover a evolução da empresa e de nossos stakeholders, bem como o da comunidade, encontrando soluções de maneira eficaz.",
        "vision": "Ser a melhor distribuidora de produtos hidráulicos da América Latina.",
        "values": "Assumir plena responsabilidade dos compromissos, resultados rápidos e da qualidade perante os colaboradores, clientes, acionistas e parceiros. Atitude crítica, dedicação para com a qualidade e melhoramento pessoal.",
        "units": [
            {"role": "MATRIZ", "state": "SP", "city": "Araraquara", "address": "Av. Engenheiro Camilo Dinucci, 4101 - Jardim Arco-Íris - CEP 14.808-100", "phone": "(16) 3508-1300"},
            {"role": "FILIAL", "state": "SP", "city": "Sertãozinho", "address": "Av. Nelson Benedito Machado Pontal, 724 - CINEP - CEP 14.176-110", "phone": "(16) 3508-1300"},
            {"role": "FILIAL", "state": "MS", "city": "Dourados", "address": "Av. Marcelino Pires, 6750 - Jardim Márcia - CEP 79.841-000", "phone": "(16) 3508-1300"},
            {"role": "FILIAL", "state": "MS", "city": "Três Lagoas", "address": "Av. Clodoaldo Garcia, 1909 - Vila Guanabara - CEP 79.621-432", "phone": "(16) 3508-1300"},
            {"role": "FILIAL", "state": "MS", "city": "Ribas do Rio Pardo", "address": "Av. Aureliano Moura Brando, 1945 - Parque Estoril - CEP 79.184-160", "phone": "(16) 3508-1300"},
            {"role": "FILIAL", "state": "BA", "city": "Teixeira de Freitas", "address": "Consulte-nos pelo telefone (16) 3508-1300", "phone": "(16) 3508-1300"},
            {"role": "FILIAL", "state": "MA", "city": "Açailândia", "address": "Consulte-nos pelo telefone (16) 3508-1300", "phone": "(16) 3508-1300"},
            {"role": "FILIAL", "state": "SC", "city": "Itajaí", "address": "Consulte-nos pelo telefone (16) 3508-1300", "phone": "(16) 3508-1300"},
        ],
    }


# --- Startup: seed admin, categories, products ---
DEFAULT_CATEGORIES = [
    {"name": "Hidráulica", "slug": "hidraulica"},
    {"name": "Pneumática", "slug": "pneumatica"},
    {"name": "Mangueiras e Conexões", "slug": "mangueiras-conexoes"},
    {"name": "Filtros e Filtração", "slug": "filtros"},
    {"name": "Vedações", "slug": "vedacoes"},
    {"name": "Rolamentos e Mancais", "slug": "rolamentos"},
    {"name": "Chicotes Elétricos", "slug": "chicotes-eletricos"},
    {"name": "Ferragens", "slug": "ferragens"},
]

SEED_PRODUCTS = [
    # Bombas Hidráulicas (série WK / WP)
    {"code": "WK10002", "name": "Bomba de Engrenagens Externas Parker", "category": "hidraulica", "brand": "Parker",
     "description": "Bomba hidráulica de engrenagens externas, alta eficiência volumétrica. Indicada para unidades hidráulicas industriais e móbile.",
     "image_url": "/hidrara/products/WK10002.png", "featured": True},
    {"code": "WK11001X", "name": "Bomba de Pistões Axiais Parker", "category": "hidraulica", "brand": "Parker",
     "description": "Bomba de pistões axiais de deslocamento variável. Ideal para prensas hidráulicas, injetoras e máquinas de alta pressão.",
     "image_url": "/hidrara/products/WK11001X.png", "featured": True},
    {"code": "WP11102", "name": "Bomba Hidráulica Parker", "category": "hidraulica", "brand": "Parker",
     "description": "Bomba hidráulica de alta performance da linha Parker, aplicação industrial e agrícola.",
     "image_url": "/hidrara/products/WP11102.png"},
    # Válvulas / Controles Hidráulicos (série X)
    {"code": "X002103", "name": "Válvula de Controle Hidráulico Parker", "category": "hidraulica", "brand": "Parker",
     "description": "Válvula de controle hidráulico de precisão. Indicada para sistemas de acionamento em máquinas industriais e agrícolas.",
     "image_url": "/hidrara/products/X002103.png"},
    {"code": "X002277", "name": "Válvula Hidráulica Parker", "category": "hidraulica", "brand": "Parker",
     "description": "Válvula hidráulica de precisão para controle direcional e de fluxo em sistemas de alta pressão.",
     "image_url": "/hidrara/products/X002277.png"},
    {"code": "X220184", "name": "Válvula Direcional CETOP Parker", "category": "hidraulica", "brand": "Parker",
     "description": "Válvula direcional 4/3 vias com acionamento solenoide, aplicação industrial em prensas e injetoras.",
     "image_url": "/hidrara/products/X220184.png", "featured": True},
    {"code": "X220185", "name": "Válvula Direcional CETOP Parker", "category": "hidraulica", "brand": "Parker",
     "description": "Válvula direcional 4/3 vias com centro tandem, aplicação industrial de alta durabilidade.",
     "image_url": "/hidrara/products/X220185.png"},
    # Chicote elétrico
    {"code": "X770733", "name": "Chicote Elétrico Industrial", "category": "chicotes-eletricos", "brand": "Parker",
     "description": "Chicote elétrico para aplicação industrial, terminais estanhados e proteção anti-UV.",
     "image_url": "/hidrara/products/X770733.png"},
    # Rolamentos e Mancais (série REL)
    {"code": "REL-100", "name": "Rolamento Rígido de Esferas", "category": "rolamentos", "brand": "SKF",
     "description": "Rolamento rígido de esferas de alta durabilidade, aplicação universal em transmissões e implementos.",
     "image_url": "/hidrara/products/REL-100.png"},
    {"code": "REL-802", "name": "Mancal Agrícola Blindado", "category": "rolamentos", "brand": "NTN",
     "description": "Mancal com rolamento incorporado, ideal para transportadores e implementos agrícolas.",
     "image_url": "/hidrara/products/REL-802.png"},
    {"code": "REL-803", "name": "Mancal Agrícola UCP", "category": "rolamentos", "brand": "NTN",
     "description": "Mancal blindado padrão UCP para fixação por pé, aplicação em maquinário agrícola e industrial.",
     "image_url": "/hidrara/products/REL-803.png"},
    # Vedações (série REC)
    {"code": "REC-153", "name": "Kit de Vedação O-Ring", "category": "vedacoes", "brand": "Parker",
     "description": "Kit sortido de anéis O-Ring em NBR 70 Shore. Kit essencial para manutenção hidráulica e pneumática.",
     "image_url": "/hidrara/products/REC-153.png"},
    {"code": "REC-154", "name": "Kit de Vedação Retentor", "category": "vedacoes", "brand": "Parker",
     "description": "Kit sortido de retentores para eixos rotativos em aplicações industriais e agrícolas.",
     "image_url": "/hidrara/products/REC-154.png"},
    {"code": "REC-672", "name": "Vedação Hidráulica Parker", "category": "vedacoes", "brand": "Parker",
     "description": "Elemento de vedação hidráulico de alta durabilidade para cilindros e bombas.",
     "image_url": "/hidrara/products/REC-672.png"},
    {"code": "REC-888", "name": "Vedação Industrial", "category": "vedacoes", "brand": "Parker",
     "description": "Vedação industrial para aplicação em componentes hidráulicos e pneumáticos.",
     "image_url": "/hidrara/products/REC-888.png"},
    {"code": "REC-906", "name": "Vedação Compact Parker", "category": "vedacoes", "brand": "Parker",
     "description": "Vedação compacta de alta performance para cilindros hidráulicos.",
     "image_url": "/hidrara/products/REC-906.png"},
    # Filtros Parker Racor (série P954/P956/P958)
    {"code": "P954208", "name": "Filtro Hidráulico Parker Racor", "category": "filtros", "brand": "Parker Racor",
     "description": "Filtro hidráulico de retorno Parker Racor, elemento filtrante em microfibra de vidro. Alta capacidade de retenção de contaminantes.",
     "image_url": "/hidrara/products/P954208.png", "featured": True},
    {"code": "P954869", "name": "Filtro Combustível Parker Racor", "category": "filtros", "brand": "Parker Racor",
     "description": "Filtro de combustível de alta performance para motores diesel industriais e agrícolas. Retém água e partículas.",
     "image_url": "/hidrara/products/P954869.png"},
    {"code": "P956639", "name": "Filtro Separador Parker Racor", "category": "filtros", "brand": "Parker Racor",
     "description": "Filtro separador água-combustível Parker Racor, indicado para colheitadeiras, tratores e caminhões.",
     "image_url": "/hidrara/products/P956639.png"},
    {"code": "P958225", "name": "Filtro de Ar Industrial Parker", "category": "filtros", "brand": "Parker",
     "description": "Filtro de ar de alta capacidade para compressores industriais e sistemas pneumáticos.",
     "image_url": "/hidrara/products/P958225.png", "featured": True},
    {"code": "P958404", "name": "Filtro Racor Diesel", "category": "filtros", "brand": "Parker Racor",
     "description": "Filtro diesel Parker Racor de alta eficiência, aplicação em motores agrícolas e industriais pesados.",
     "image_url": "/hidrara/products/P958404.png"},
    # Mangueiras e Conexões
    {"code": "PERI-340-10C", "name": "Mangueira Hidráulica Parker", "category": "mangueiras-conexoes", "brand": "Parker",
     "description": "Mangueira hidráulica 2 tramas de aço, sistema Parkrimp certificado, alta pressão de trabalho.",
     "image_url": "/hidrara/products/PERI-340-10C.png", "featured": True},
    {"code": "PER-67", "name": "Conexão Hidráulica Parker", "category": "mangueiras-conexoes", "brand": "Parker",
     "description": "Conexão hidráulica padrão JIC, aço carbono zincado, compatível com sistema Parkrimp.",
     "image_url": "/hidrara/products/PER-67.png"},
    # Pneumática
    {"code": "PF420", "name": "Atuador Pneumático Parker", "category": "pneumatica", "brand": "Parker",
     "description": "Cilindro pneumático ISO 15552 com amortecimento pneumático regulável, aplicação industrial.",
     "image_url": "/hidrara/products/PF420.png", "featured": True},
]


async def seed_admin():
    existing = await db.users.find_one({"email": ADMIN_EMAIL})
    if existing is None:
        await db.users.insert_one({
            "id": str(uuid.uuid4()),
            "email": ADMIN_EMAIL,
            "password_hash": hash_password(ADMIN_PASSWORD),
            "name": "Administrador",
            "role": "admin",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info(f"Admin seeded: {ADMIN_EMAIL}")
    elif not verify_password(ADMIN_PASSWORD, existing["password_hash"]):
        await db.users.update_one(
            {"email": ADMIN_EMAIL},
            {"$set": {"password_hash": hash_password(ADMIN_PASSWORD)}},
        )
        logger.info(f"Admin password refreshed: {ADMIN_EMAIL}")


async def seed_categories():
    for c in DEFAULT_CATEGORIES:
        exists = await db.categories.find_one({"slug": c["slug"]})
        if not exists:
            await db.categories.insert_one({"id": str(uuid.uuid4()), **c})


async def seed_products():
    count = await db.products.count_documents({})
    if count > 0:
        return
    now = datetime.now(timezone.utc).isoformat()
    docs = []
    for p in SEED_PRODUCTS:
        docs.append({
            "id": str(uuid.uuid4()),
            "code": p["code"],
            "name": p["name"],
            "category": p["category"],
            "description": p.get("description", ""),
            "image_url": p.get("image_url", ""),
            "brand": p.get("brand", ""),
            "stock": "available",
            "featured": p.get("featured", False),
            "created_at": now,
            "updated_at": now,
        })
    if docs:
        await db.products.insert_many(docs)
        logger.info(f"Seeded {len(docs)} products")


@app.on_event("startup")
async def startup():
    await db.users.create_index("email", unique=True)
    await db.users.create_index("id", unique=True)
    await db.products.create_index("id", unique=True)
    await db.products.create_index("code", unique=True)
    await db.products.create_index([("name", "text"), ("code", "text"), ("description", "text")])
    await db.categories.create_index("slug", unique=True)
    await seed_admin()
    await seed_categories()
    await seed_products()
    init_storage()


@app.on_event("shutdown")
async def shutdown():
    client.close()


@api_router.get("/")
async def root():
    return {"service": "Hidrara API", "status": "ok"}


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
