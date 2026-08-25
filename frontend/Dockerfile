# 知源前端镜像：node 构建静态产物 → nginx 托管并反代后端。
# 构建上下文是**仓库根**（与 backend/Dockerfile 一致）：
#   docker build -f frontend/Dockerfile .
FROM node:20-alpine AS build

# 国内服务器可传镜像源：--build-arg NPM_REGISTRY=https://registry.npmmirror.com
ARG NPM_REGISTRY=https://registry.npmjs.org/

WORKDIR /app
# 先只拷 manifest：改业务代码不触发重新装包
COPY frontend/package.json frontend/package-lock.json ./
RUN npm config set registry "$NPM_REGISTRY" && npm ci

COPY frontend/ ./
# tsc -b && vite build —— 类型错误会让构建失败，这是有意的（别改成只跑 vite build）
RUN npm run build

FROM nginx:1.27-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
