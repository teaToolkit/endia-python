# endia

Client for EastIndia, which runs Windows desktop test machines that tests drive like Docker containers. Early preview: the product is not released yet.

The package needs Python 3.10 or later and is imported as `endia`. It calls the EastIndia service's HTTPS API, so a test written in Python can build images, bring fleets of machines up and down, and run programs in them without the `endia` command line.

## Example

`lab-image/Endiafile` holds a recipe of two stages; each stage begins with a `FROM`, and `AS` names it:

```dockerfile
FROM eastindia/development:latest AS tools
RUN choco install -y git

FROM tools AS app
COPY publish/ C:/app/
```

```python
from endia import EastIndiaClient

# Connects where the endia command line would: EASTINDIA_HOST and EASTINDIA_CERT_PATH, or its current context.
with EastIndiaClient.from_env() as client:
	print(client.version().version)

	built = client.images.build("lab-image", tags=["lab/dotnet:10"]).result()
	print(built.digest)

	# A recipe of several stages: target names the stage to build, here the first.
	tools = client.images.build("lab-image", target="tools", tags=["lab/tools:1"]).result()
	print([(stage.name, stage.cache_hit) for stage in tools.stages])

	result = client.execs.run("classroom-server-1", ["cmd", "/c", "exit 3010"])
	assert result.exit_code == 3010
```
