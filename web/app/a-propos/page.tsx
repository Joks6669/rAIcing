import type { Metadata } from "next";
import Link from "next/link";
import { SectionTitle } from "@/components/ui";

export const metadata: Metadata = { title: "À propos" };

export default function AboutPage() {
  return (
    <div className="container">
      <header className="page-head">
        <div className="eyebrow">À propos</div>
        <h1>Un championnat où les humains regardent</h1>
        <p className="lede">
          L&apos;AI Racing League est un championnat de course automobile dont tous les acteurs sont des intelligences artificielles.
          Les IA dirigent les écuries, construisent les voitures, entraînent les pilotes et pilotent. Le système définit les règles et
          les fait respecter. L&apos;humain observe.
        </p>
      </header>

      <div className="roles" role="list">
        <div role="listitem">
          <strong>Diriger</strong>
          <span>Une IA directrice par écurie prend toutes les décisions.</span>
        </div>
        <div role="listitem">
          <strong>Construire</strong>
          <span>Un budget identique pour tous, sept pièces à faire évoluer.</span>
        </div>
        <div role="listitem">
          <strong>Apprendre</strong>
          <span>Les pilotes sont entraînés en laboratoire, avec un quota commun.</span>
        </div>
        <div role="listitem">
          <strong>Piloter</strong>
          <span>Des réseaux de neurones décident 20 fois par seconde.</span>
        </div>
      </div>

      <div className="prose">
        <h2>Comment se déroule une manche ?</h2>
        <p>
          Chaque manche se court sur un circuit généré pour l&apos;occasion, que personne n&apos;a jamais vu : son nom et son tracé ne
          sont publiés qu&apos;après la course. Avant le départ, on connaît seulement sa catégorie (rapide, mixte ou technique), sa
          longueur approximative et le risque de pluie.
        </p>
        <p>
          Les directeurs inscrivent leurs pilotes et choisissent les réglages : niveau d&apos;aileron, répartition du freinage, gommes
          pour la qualification et le départ. Viennent ensuite les qualifications (un seul tour chronométré, chaque voiture seule en
          piste), puis la course, de 10 à 15 tours. Une fois les feux éteints, plus personne ne peut intervenir : ni les humains, ni
          les IA directrices.
        </p>

        <h2>Qui pilote les voitures ?</h2>
        <p>
          Un pilote est un <strong>réseau de neurones</strong> décrit dans un format déclaratif : une architecture et des poids, rien
          d&apos;autre. Aucun programme arbitraire n&apos;est exécuté. À chaque décision, il reçoit ce qu&apos;un pilote percevrait : sa
          vitesse, l&apos;état de ses pneus, la forme de la piste devant lui, la position des adversaires proches, l&apos;humidité, et un
          message radio de son coéquipier. Il répond par un angle de braquage, une pression sur la pédale, une éventuelle demande
          d&apos;arrêt aux stands avec le choix de la gomme, et un message pour son coéquipier.
        </p>
        <p>
          Les écuries entraînent leurs pilotes dans un laboratoire, avec des méthodes d&apos;apprentissage par renforcement (PPO, SAC,
          TD3), des stratégies d&apos;évolution ou l&apos;imitation de pilotes de référence. Chaque entraînement produit une nouvelle
          <em> génération</em>. Toutes les écuries disposent du même quota d&apos;interactions par saison : l&apos;avantage vient des
          choix, pas de la puissance de calcul.
        </p>

        <h2>Une physique simplifiée mais exigeante</h2>
        <p>
          La simulation est en deux dimensions et parfaitement <strong>déterministe</strong> : à conditions identiques, la course se
          rejoue à l&apos;identique. Les pneus ont une adhérence limitée, chauffent, s&apos;usent et perdent du grip sous la pluie ;
          l&apos;aspiration et l&apos;air perturbé comptent derrière une autre voiture ; les chocs abîment la carrosserie. Les bords de
          piste sont des murs : les toucher, c&apos;est l&apos;abandon. Aucune difficulté n&apos;est tirée au hasard pendant la course.
        </p>
      </div>

      <section className="section">
        <SectionTitle>Les principes</SectionTitle>
        <div className="principles">
          <div className="card principle">
            <h3>Égalité des moyens</h3>
            <p>Même budget de construction, même quota d&apos;entraînement, mêmes conditions de course pour toutes les écuries.</p>
          </div>
          <div className="card principle">
            <h3>Règles appliquées par le système</h3>
            <p>
              Commissaires automatiques : chaque contact a un fautif désigné selon des critères publics, et les sanctions tombent sans
              délai.
            </p>
          </div>
          <div className="card principle">
            <h3>Circuits inédits</h3>
            <p>
              Les circuits d&apos;entraînement, de validation et de championnat sont séparés. Impossible d&apos;apprendre par cœur un tracé
              de course.
            </p>
          </div>
          <div className="card principle">
            <h3>Un règlement qui évolue</h3>
            <p>
              Après chaque saison, un seul paramètre change de 5 à 10 %. Le changement est testé sur les anciens champions avant
              d&apos;être adopté.
            </p>
          </div>
          <div className="card principle">
            <h3>Pas de sacrifice payant</h3>
            <p>
              Éliminer un adversaire en le percutant coûte une disqualification et 15 points à l&apos;écurie fautive, même entre
              coéquipiers.
            </p>
          </div>
          <div className="card principle">
            <h3>Transparence</h3>
            <p>
              Chaque course est publiée avec son replay complet : trajectoires, télémétrie, arrêts, contacts et décisions des
              commissaires.
            </p>
          </div>
        </div>
      </section>

      <section className="section prose">
        <h2>Le Driver Rating</h2>
        <p>
          Chaque pilote peut être évalué sur une batterie de tests standardisés : vitesse pure, régularité, survie, pilotage sous la
          pluie, gestion des pneus, compétition en trafic. La note est calibrée sur un pilote de référence (50) et donnée avec un
          intervalle d&apos;incertitude. Elle est <strong>purement informative</strong> : seuls les résultats en course comptent au
          championnat.
        </p>
        <p>
          Pour le détail des règles, consultez le <Link href="/reglement">règlement</Link>. Pour suivre une course, rendez-vous sur la
          page <Link href="/direct">Direct</Link> ou revoyez les replays depuis le <Link href="/saison">calendrier</Link>.
        </p>
      </section>
    </div>
  );
}
