# ingress
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.12.0/cert-manager.crds.yaml
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.12.0/cert-manager.yaml

kubectl apply -f clusterissuer.yaml

kubectl get clusterissuer letsencrypt-prod
kubectl describe clusterissuer letsencrypt-prod

kubectl apply -f middlewares.yaml

kubectl apply -f my-ingress.yaml

kubectl get ingress -A | grep acme

kubectl describe certificate my-tls-secret -n default

curl -vk https://pmqxyz.hopto.org


