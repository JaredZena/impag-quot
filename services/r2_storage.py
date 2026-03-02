import boto3
from botocore.config import Config
from config import r2_account_id, r2_access_key_id, r2_secret_access_key, r2_bucket_name

VALID_CATEGORIES = {
    'cotizacion',
    'nota',
    'factura',
    'comprobante-de-pago',
    'project-image',
    'packaging-logistics',
    'whatsapp-chat',
    'ficha-tecnica',
    'imagen-de-producto',
    'infografia',
    'article',
    'control-de-ventas',
    'catalogo',
    'estado-de-cuenta',
}

MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB


def get_r2_client():
    """Create and return an S3-compatible client for Cloudflare R2."""
    return boto3.client(
        's3',
        endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
        aws_access_key_id=r2_access_key_id,
        aws_secret_access_key=r2_secret_access_key,
        config=Config(
            signature_version='s3v4',
            region_name='auto',
        ),
        verify=False,
    )


def build_file_key(category: str, file_id: int, original_filename: str) -> str:
    """Build the R2 object key from category, ID, and original filename."""
    return f"{category}/{file_id}/{original_filename}"


def upload_file(file_key: str, file_content: bytes, content_type: str) -> None:
    """Upload a file to R2."""
    client = get_r2_client()
    client.put_object(
        Bucket=r2_bucket_name,
        Key=file_key,
        Body=file_content,
        ContentType=content_type,
    )


def generate_presigned_download_url(file_key: str, expires_in: int = 900) -> str:
    """Generate a presigned URL for downloading a file (default 15 min)."""
    client = get_r2_client()
    return client.generate_presigned_url(
        'get_object',
        Params={'Bucket': r2_bucket_name, 'Key': file_key},
        ExpiresIn=expires_in,
    )


def generate_presigned_view_url(file_key: str, content_type: str, expires_in: int = 900) -> str:
    """Generate a presigned URL for inline viewing (Content-Disposition: inline)."""
    client = get_r2_client()
    return client.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': r2_bucket_name,
            'Key': file_key,
            'ResponseContentDisposition': 'inline',
            'ResponseContentType': content_type,
        },
        ExpiresIn=expires_in,
    )


def delete_file(file_key: str) -> None:
    """Delete a file from R2."""
    client = get_r2_client()
    client.delete_object(Bucket=r2_bucket_name, Key=file_key)
