## Git Commands
Specify the ```GIT_EMAIL``` and ```GIT_NAME``` variables for this to work. They are used to set up git correctly and allow the following commands to work.

### Push code to Git
To push code changes made within the container back to git run:
```
sudo docker exec -t -i <CONTAINER_NAME> /usr/bin/push
```
### Pull code from Git (disabled)

`/usr/bin/pull` exits with an error without changing code or ownership. Build a new application image and replace the container instead of updating a running checkout.
