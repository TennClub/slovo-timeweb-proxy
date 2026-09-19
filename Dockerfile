FROM nginx:1.27-alpine

COPY default.conf /etc/nginx/conf.d/default.conf
COPY slovo-study-context-update.tar.gz /usr/share/nginx/html/slovo-study-context-update.tar.gz

EXPOSE 8080
