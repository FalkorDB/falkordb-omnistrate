# Deploy a Grafana instance to visualize metrics for FalkorDB Deployments

## Deploy Grafana

In the dashboard, find the Grafana resource on the left-side menu.
Deploy a new instance of Grafana by clicking the `+ Create` button.
Enable SMTP if you'd like to receive alerts via email.


## Setup Prometheus Data Source

Once the deployment is ready and RUNNING, click on the instance and go to the Connection tab.
There you can copy the Global endpoint and port, to access the UI.
Once you log in using the credentials setup in the previous step, you can add a new Prometheus data source by going to Connections > Data Sources > Add Data Source.

Fill in the following field:
Host: http://prometheus-operated.observability-ns.svc.cluster.local:9090

Click Save & Test to verify the connection.

## Import Dashboards

You can import dashboards from the Grafana marketplace or create your own.

To import our pre-configured dashboard, go to the dashboard menu and click on Import.
Use the following dashboard ID: 22227