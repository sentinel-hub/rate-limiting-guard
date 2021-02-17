## Tests

To run end-to-end tests, first install the python packages needed:
```
$ cd e2etest/
$ pipenv install
```

Then:
```
$ docker-compose up -d
$ pipenv shell
<pipenv> $ pytest
```
