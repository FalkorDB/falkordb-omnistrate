### REDIS OPERATOR

## Why ?
- The number of maintainers of the project.
- taking control of several deployment aspects that today only omnistrate has control of.
- we dont have to create a separate sharding script.
- we don't to create a rebalancer instance.
- less manual configuration.

## Features that are added/to be added:
- [x] Now the operator supports the use of wild card with TLS certificates (*.namespace).
- [ ] Working on adding host port for the pods (kubernetes) to allow direct communication using the port of the pod (kubernetes) and the public ip of the VM (node).
- [ ] Fix the FLUSHALL command that causes the data loss when doing a helm uninstall and reinstall.

## Important points/aspects of the operator to take into consideration:
- [ ] TLS creation is going to be our responsibility.
- [ ] We have to take care of the kubernetes services (operator only creates one that forwards to all pods).
- [ ] The helm charts are not up to date, meaning adding some options to the values yaml are not reflected in the created CRD, we have to create the CRD and edit.
- [ ] We have to override the Liveness and Readiness probes to fit our needs.
- [ ] We have to take care of POD distribution in multizone and zone enforcement in singlezone.
- [ ] Data loss when doing a helm uninstall due to the FLUSHALL command (temp fix: rename the command)
- [ ] We still need a CRON job to issue a BGRWRITEAOF
