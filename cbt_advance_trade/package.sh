
pwd

rm -rf package
rm cb_advance_trade-package.zip

pip install --target ./package -r aws_dependency.txt

cd package

zip -r ../cb_advance_trade-package.zip .

cd ..

zip -g cb_advance_trade-package.zip lambda_function.py
zip -g cb_advance_trade-package.zip cb_auth.py
zip -g cb_advance_trade-package.zip coinbase_client.py
zip -g cb_advance_trade-package.zip config.py
zip -g cb_advance_trade-package.zip secrets.conf