# Kubernetes

Deploy local-agents (CLI + web board + MCP) len cluster.

## 1. Yeu cau

- Kubernetes >= 1.24
- `kubectl` + `kustomize` (kubectl >= 1.14 co san kustomize)
- Image da push len registry: `ghcr.io/maxxalan/ngu:latest`
  ```bash
  docker build -t ghcr.io/maxxalan/ngu:latest .
  docker push ghcr.io/maxxalan/ngu:latest
  ```
  (CI tu dong push image khi merge vao `main`)

## 2. Secret (token HF - khong bat buoc)

```bash
cp k8s/20-secret.example.yaml k8s/secret.yaml
# sua HF_TOKEN trong k8s/secret.yaml
kubectl apply -f k8s/secret.yaml
```

## 3. Apply

```bash
kubectl apply -k k8s/
kubectl -n ngu get pods -w
```

## 4. Truy cap

```bash
# trong cluster (port-forward)
kubectl -n ngu port-forward svc/ngu 8765:8765
# -> http://127.0.0.1:8765/   (web board)
# -> http://127.0.0.1:8765/mcp (MCP)

# hoac tu may khac trong cluster
kubectl -n ngu get svc ngu-nodeport   # NodePort 30876
```

Dang ky MCP cho opencode (may local):

```bash
opencode mcp add local-agents --url http://<node-ip>:30876/mcp --global
```

## 5. Model

- `initContainer: fetch-models` tai model tu HuggingFace vao PVC `ngu-models`
  (dung `scripts/models.json` lam manifest chung voi `setup.bat`).
- PVC mac dinh 50Gi - doi trong `30-pvc.yaml` neu can nhieu hon.
- Tai lai model: xoa pod de initContainer chay lai:
  ```bash
  kubectl -n ngu delete pod -l app.kubernetes.io/name=ngu
  ```

## 6. GPU (NVIDIA)

1. Cai NVIDIA device plugin tren cluster.
2. Build image voi backend CUDA:
   ```bash
   docker build --build-arg LLAMA_BACKEND=cuda-12.8 -t ghcr.io/maxxalan/ngu:cuda .
   ```
3. Bo comment block `nvidia.com/gpu` trong `40-deployment.yaml` va doi image sang tag `cuda`.

## 7. File trong thu muc nay

| file | vai tro |
| --- | --- |
| `00-namespace.yaml` | namespace `ngu` |
| `10-configmap.yaml` | noi dung `.config` (cung key voi app/config.py) |
| `20-secret.example.yaml` | mau secret (HF_TOKEN) |
| `30-pvc.yaml` | persistent volume cho model |
| `40-deployment.yaml` | deployment + initContainer tai model |
| `50-service.yaml` | ClusterIP + NodePort |
| `60-ingress.yaml` | ingress tuy chon (nginx) |
| `kustomization.yaml` | gom manifest de apply bang `kubectl apply -k` |
