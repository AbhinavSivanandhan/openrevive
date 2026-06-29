# OpenRevive deployment

Initial setup:

    make bootstrap
    make up

Deploy with a deterministic public Python documentation demo:

    DEMO=python-docs make up

Or seed the demo after deployment:

    make seed-demo

Cost controls:

    make aws-stop
    make aws-down
    CONFIRM=DELETE_DEMO_DATA make aws-nuke

`aws-stop` sets API and worker desired count to zero.

`aws-down` destroys only runtime resources: ALB, ECS services, task definitions, scheduler, runtime DNS alias, and log groups. Aurora, S3 artifacts, ECR images, and secrets stay available.

`aws-nuke` requires the exact confirmation value and destroys demo data plus foundation infrastructure.

The Vercel project is not deleted by AWS lifecycle commands.
