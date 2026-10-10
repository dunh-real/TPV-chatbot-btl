# LibreOffice headless chỉ để chuyển .doc -> .docx cho app.rag.doc_convert.
# Build: docker build -t tpv-libreoffice:7.3 backend/docker/libreoffice
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice-writer-nogui fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /work
