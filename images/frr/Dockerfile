FROM quay.io/frrouting/frr:10.3.1

# Our entrypoint waits for the Operator to deliver configs via
# kubectl exec (cp + sentinel touch), then hands off to FRR's
# stock docker-start which uses watchfrr to manage daemons.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["/entrypoint.sh"]
