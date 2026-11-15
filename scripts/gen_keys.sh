#!/bin/sh
set -eu

output_dir="${1:-secrets}"
private_key="$output_dir/jwt_private.pem"
public_key="$output_dir/jwt_public.pem"

if [ -e "$private_key" ] || [ -e "$public_key" ]; then
    echo "JWT key files already exist in $output_dir; move them aside before rotating keys." >&2
    exit 1
fi

mkdir -p "$output_dir"
umask 077
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$private_key"
openssl pkey -in "$private_key" -pubout -out "$public_key"
chmod 600 "$private_key"
chmod 644 "$public_key"
echo "Created $private_key and $public_key. Keep the private key out of Git."
