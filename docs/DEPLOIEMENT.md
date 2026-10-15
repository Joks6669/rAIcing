# Mettre les Rencontres en ligne gratuitement (Hugging Face Spaces)

Résultat : une adresse publique du type `https://<votre-nom>-raicing.hf.space` où l'on dépose les modèles,
lance les rencontres et regarde les replays, depuis un ordinateur ou un téléphone. Gratuit.

Durée : une dizaine de minutes de manipulations, puis 5 à 10 minutes de construction automatique.

## 1. Créer le compte

Sur https://huggingface.co, cliquez sur **Sign Up** et créez un compte gratuit (adresse e-mail à confirmer).

## 2. Créer le Space

1. En haut à droite : votre avatar, puis **New Space**.
2. Remplissez :
   - **Space name** : `raicing` (ou un autre nom) ;
   - **License** : laissez vide ;
   - **Select the Space SDK** : **Docker**, puis le modèle **Blank** ;
   - **Space hardware** : **CPU basic · 2 vCPU · 16 GB · FREE** ;
   - **Public** (les visiteurs peuvent regarder ; seuls ceux qui ont le mot de passe lancent des rencontres).
3. Cliquez sur **Create Space**.

## 3. Choisir le mot de passe

Dans le Space : **Settings**, rubrique **Variables and secrets**, bouton **New secret** :

- **Name** : `RAICING_MATCH_PASSWORD`
- **Value** : le mot de passe de votre choix (à donner à ceux qui peuvent lancer des rencontres).

Sans ce secret, tout visiteur pourrait lancer des rencontres.

## 4. Déposer les deux fichiers

Dans l'onglet **Files** du Space :

1. Ouvrez `README.md`, cliquez sur **edit**, remplacez tout son contenu par celui de
   [`deploy/huggingface/README.md`](../deploy/huggingface/README.md), puis **Commit changes to main**.
   (La ligne `app_port: 7860` est indispensable.)
2. **Add file**, puis **Create a new file** : nommez-le `Dockerfile`, collez le contenu de
   [`deploy/huggingface/Dockerfile`](../deploy/huggingface/Dockerfile), puis **Commit new file to main**.

La construction démarre aussitôt (onglet **App**, mention *Building*). Quand elle passe à *Running*,
l'interface s'affiche. L'adresse publique est indiquée dans le menu **⋮**, puis **Embed this Space**
(*Direct URL*).

## 5. Mettre à jour après une modification du projet

Le Space récupère le code sur GitHub (branche `main`) à chaque construction. Après un changement :
**Settings**, puis **Factory rebuild**.

Si le dépôt GitHub devient privé, ajoutez dans **Variables and secrets** un secret `GITHUB_TOKEN`
contenant un jeton GitHub en lecture seule sur ce dépôt (GitHub : Settings > Developer settings >
Fine-grained tokens, accès *Contents: Read-only*).

## 6. Limites de l'offre gratuite

- **Mise en veille** après 48 h sans visite : la visite suivante réveille le Space (environ une minute).
- **Rencontres effacées au redémarrage** (veille, reconstruction) : le stockage gratuit n'est pas
  conservé. Pensez à noter les résultats importants.
- **Une rencontre à la fois**, cinq au plus en attente ; une course complète prend une à deux minutes.

## Réglages (facultatifs, dans Variables and secrets)

| Nom | Rôle | Défaut |
|---|---|---|
| `RAICING_MATCH_PASSWORD` | mot de passe pour lancer une rencontre | aucun (ouvert à tous) |
| `RAICING_MATCH_QUEUE` | rencontres en attente acceptées au plus | 5 |

## Tester l'image chez soi (facultatif)

```bash
docker build -t raicing-space deploy/huggingface
docker run -p 7860:7860 -e RAICING_MATCH_PASSWORD=essai raicing-space
# puis http://localhost:7860
```
