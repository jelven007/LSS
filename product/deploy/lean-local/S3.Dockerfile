# Local integration emulator only; production continues to use TOS.
FROM node:20-alpine
RUN npm install --global --registry=https://registry.npmjs.org s3rver@3.7.1 \
    && mkdir /data && chown node:node /data
USER node
EXPOSE 9000
ENTRYPOINT ["s3rver"]
CMD ["--address", "0.0.0.0", "--port", "9000", "--directory", "/data"]
